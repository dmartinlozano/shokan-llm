"""Google A2A (Agent-to-Agent) protocol — Agent Card serving and task execution.

Spec: https://google.github.io/A2A/specification/

Endpoints registered in main.py:
  GET  /.well-known/agent.json  — Agent Card discovery
  POST /a2a/tasks               — Receive and execute an A2A task

Auth schemes (configured via the A2A / Agents UI tab):
  none   — open endpoint (use only in private networks)
  bearer — static Bearer token stored in K8s secret 'a2a-bearer-token'
  oauth2 — Bearer token introspected against Keycloak (placeholder, returns 501)
"""

import uuid
from collections.abc import AsyncGenerator

from fastapi import Request
from fastapi.responses import JSONResponse

from config import SHOKAN_URL, OLLAMA_URL
from connectors.k8s import K8s
from connectors.litellm import LiteLLM
from services.litellm_config import LiteLLMConfig

_A2A_SERVICE_USER = "a2a-service"


def _build_agent_card(cfg: dict, url: str) -> dict:
    """Build a Google A2A–compliant Agent Card from stored config."""
    skills = [
        {
            "id": s.get("id", ""),
            "name": s.get("name", ""),
            "description": s.get("description", ""),
        }
        for s in cfg.get("skills", [])
        if s.get("id")
    ]
    auth_scheme = cfg.get("auth_scheme", "none")
    if auth_scheme == "bearer":
        security_schemes = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
        security = [{"bearerAuth": []}]
    elif auth_scheme == "oauth2":
        security_schemes = {
            "oauth2": {
                "type": "oauth2",
                "flows": {"clientCredentials": {"tokenUrl": "", "scopes": {}}},
            }
        }
        security = [{"oauth2": []}]
    else:
        security_schemes = {}
        security = []

    return {
        "name":        cfg.get("name", "Shokan AI"),
        "description": cfg.get("description", "Shokan-LLM agentic AI platform"),
        "url":         cfg.get("url", url),
        "version":     cfg.get("version", "1.0.0"),
        "skills":      skills,
        "defaultInputModes":  ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        **({"securitySchemes": security_schemes, "security": security} if security_schemes else {}),
    }


async def _check_auth(request: Request, cfg: dict, k8s: K8s) -> JSONResponse | None:
    """Return a JSONResponse error if auth fails, None if OK."""
    scheme = cfg.get("auth_scheme", "none")
    if scheme == "none":
        return None

    if scheme == "oauth2":
        return JSONResponse(
            {"error": {"code": -32001, "message": "OAuth2 introspection not yet implemented"}},
            status_code=501,
        )

    # bearer
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return JSONResponse(
            {"error": {"code": -32001, "message": "Missing or malformed Authorization header"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    provided = auth_header[7:].strip()
    stored = k8s.read("a2a-bearer-token")
    if not stored or provided != stored:
        return JSONResponse(
            {"error": {"code": -32001, "message": "Invalid bearer token"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None


def _extract_text(message: dict) -> str:
    """Extract plain text from an A2A message (parts[*].text)."""
    parts = message.get("parts") or []
    return " ".join(p.get("text", "") for p in parts if p.get("text")).strip()


async def _collect_stream(gen: AsyncGenerator[str, None]) -> str:
    """Drain an async token generator into a single string."""
    parts: list[str] = []
    async for token in gen:
        parts.append(token)
    return "".join(parts)


async def handle_agent_card(k8s: K8s) -> JSONResponse:
    """Handler for GET /.well-known/agent.json."""
    cfg = k8s.read_json("litellm-a2a-config")
    card = _build_agent_card(cfg, SHOKAN_URL)
    return JSONResponse(card, headers={"Content-Type": "application/json"})


async def handle_task(request: Request, k8s: K8s) -> JSONResponse:
    """Handler for POST /a2a/tasks.

    Accepts a Google A2A Task object, runs it through ChatService, and returns
    the completed Task with the answer in artifacts.
    """
    from connectors.litellm import LiteLLM
    from connectors.ollama import Ollama
    from services.chat import ChatService

    cfg = k8s.read_json("litellm-a2a-config")

    auth_error = await _check_auth(request, cfg, k8s)
    if auth_error:
        return auth_error

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"code": -32700, "message": "Invalid JSON"}},
            status_code=400,
        )

    task_id = body.get("id") or str(uuid.uuid4())
    message = body.get("message") or {}
    text = _extract_text(message)

    if not text:
        return JSONResponse(
            {"error": {"code": -32600, "message": "message.parts must contain at least one text part"}},
            status_code=400,
        )

    litellm = LiteLLM()
    ollama  = Ollama()

    # Use first running Ollama model, or fall back to whatever LiteLLM has
    running = await ollama.running_models()
    if running:
        model    = f"ollama/{running[0]['name']}"
        chat_url = f"{OLLAMA_URL}/v1/chat/completions"
        headers  = {}
    else:
        models = await litellm.list_models()
        model    = models[0] if models else ""
        chat_url = litellm.url
        headers  = litellm._headers

    if not model:
        return JSONResponse(
            {"error": {"code": -32603, "message": "No AI model available"}},
            status_code=503,
        )

    history: list[dict] = [{"role": "user", "content": text}]
    chat_svc = ChatService()

    try:
        stream = await chat_svc.stream_response(
            user_id=_A2A_SERVICE_USER,
            model=model,
            history=history,
            litellm_url=chat_url,
            litellm_headers=headers,
            ollama_url=OLLAMA_URL,
        )
        answer = await _collect_stream(stream)
    except Exception as exc:
        return JSONResponse(
            {"error": {"code": -32603, "message": str(exc)}},
            status_code=500,
        )

    return JSONResponse({
        "id": task_id,
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [{"type": "text", "text": answer}],
            }
        ],
    })
