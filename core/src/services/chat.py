"""Chat orchestration service — RAG context assembly, MCP tool calling, LLM streaming.

Flow per user turn:
  1. Retrieve relevant RAG chunks and build system prompt.
  2. Collect MCP tool schemas for servers the user has access to.
  3. Agentic loop: call LLM → execute tool_calls → feed results back (up to MAX_ROUNDS).
  4. Stream the final answer token by token via an async generator.
"""

import asyncio
import json
from collections.abc import AsyncGenerator

import httpx

from connectors.k8s import K8s
from connectors.mcp import MCP, SERVERS
from connectors.mcp_client import MCPClient
from connectors.openfga import OpenFGA
from connectors.rag import Retriever
from services.audit import AuditLog
from services.skills import SkillsStorage

_MAX_TOOL_ROUNDS  = 5
_SUMMARIZE_AFTER  = 20   # total messages before condensing history
_KEEP_RECENT      = 8    # messages always sent verbatim (4 latest exchanges)


def _ollama_error(status: int, body: str) -> str:
    """Convert an Ollama HTTP error into a user-friendly message."""
    low = body.lower()
    if "model" in low and ("not found" in low or "pull" in low):
        return (
            "⚠️ Model not installed in Ollama. "
            "Go to **Models → Manage models** to download it first."
        )
    if status == 500 and ("no such" in low or "unknown model" in low):
        return (
            "⚠️ Model not found in Ollama. "
            "Go to **Models → Manage models** to download it first."
        )
    try:
        import json
        msg = json.loads(body).get("error", body)
    except Exception:
        msg = body
    return f"[Error {status}] {str(msg)[:300]}"


class ChatService:
    """Orchestrates the full Shokan chat turn: RAG → LLM → MCP tools → streaming answer."""

    def __init__(self) -> None:
        self._retriever = Retriever()
        self._k8s = K8s()
        self._fga = OpenFGA()
        self._mcp_cfg = MCP(self._k8s, self._fga)
        self._audit = AuditLog()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def stream_response(
        self,
        *,
        user_id: str,
        model: str,
        history: list[dict],
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str,
        temperature: float = 0.0,
    ) -> AsyncGenerator[str, None]:
        """Yield answer tokens for the current turn.

        history: full conversation so far, last entry must be role=user.
        temperature: 0.0 = deterministic, 1.0 = maximum creativity.
        """
        return self._run(
            user_id=user_id,
            model=model,
            history=history,
            litellm_url=litellm_url,
            litellm_headers=litellm_headers,
            ollama_url=ollama_url,
            temperature=temperature,
        )

    # ── Internal orchestration ─────────────────────────────────────────────────

    async def _run(
        self,
        *,
        user_id: str,
        model: str,
        history: list[dict],
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str,
        temperature: float = 0.0,
    ) -> AsyncGenerator[str, None]:
        user_query = next(
            (m["content"] for m in reversed(history) if m["role"] == "user"), ""
        )

        # 1. Active skills + RAG context
        skills, chunks = await asyncio.gather(
            asyncio.to_thread(SkillsStorage().enabled_skills),
            self._retriever.retrieve(user_query, user_id=user_id),
        )

        skill_block = ""
        if skills:
            skill_block = "\n\n".join(
                f"## {s['name']}\n\n{s['content']}" for s in skills
            )

        if chunks:
            context = "\n\n---\n\n".join(chunks)
            system_content = (
                "You are Shokan, a helpful AI assistant.\n\n"
                "Use the following context from the knowledge base to answer the user's question. "
                "If the context is not relevant, answer from your general knowledge.\n\n"
                f"CONTEXT:\n{context}"
            )
        else:
            system_content = "You are Shokan, a helpful AI assistant."

        if skill_block:
            system_content += f"\n\n## Behavior Instructions\n\n{skill_block}"

        # Route: Ollama models go directly to Ollama's OpenAI-compatible API;
        # cloud models go through LiteLLM.
        is_ollama = model.startswith("ollama/")
        ollama_model = model[len("ollama/"):] if is_ollama else model
        chat_url = f"{ollama_url}/v1/chat/completions" if is_ollama else f"{litellm_url}/chat/completions"
        chat_headers = {} if is_ollama else litellm_headers

        condensed = await self._condense_history(
            history, ollama_url, chat_url, ollama_model if is_ollama else model, chat_headers
        )
        messages = [{"role": "system", "content": system_content}, *condensed]

        # 2. MCP tools available to this user
        tools, mcp_client = await self._collect_tools(user_id)

        # 3. Agentic tool-calling loop (blocking rounds before final stream)
        for _round in range(_MAX_TOOL_ROUNDS):
            payload: dict = {
                "model": ollama_model if is_ollama else model,
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            async with httpx.AsyncClient() as http:
                r = await http.post(
                    chat_url,
                    json=payload,
                    headers=chat_headers,
                    timeout=120.0,
                )

            if not r.is_success:
                yield _ollama_error(r.status_code, r.text) if is_ollama else f"[Error {r.status_code}] {r.text[:200]}"
                return

            choice = (r.json().get("choices") or [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                break  # no tools requested → proceed to streaming final answer

            # Execute all tool calls concurrently
            messages.append(message)
            results = await asyncio.gather(
                *[self._run_tool(tc, mcp_client, user_id) for tc in tool_calls]
            )
            for tc, result in zip(tool_calls, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        # 4. Stream final answer
        await self._audit.log(
            user_id, "chat", model, {"tools_used": bool(tools), "query_len": len(user_query)}
        )

        stream_payload: dict = {
            "model": ollama_model if is_ollama else model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }

        async with httpx.AsyncClient() as http:
            async with http.stream(
                "POST",
                chat_url,
                json=stream_payload,
                headers=chat_headers,
                timeout=120.0,
            ) as response:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                        delta = chunk["choices"][0]["delta"].get("content") or ""
                        if delta:
                            yield delta
                    except Exception:
                        continue

    async def _collect_tools(self, user_id: str) -> tuple[list[dict], MCPClient]:
        """Return (tool_schemas, client) for MCP servers the user can_use and are configured."""
        client = MCPClient(self._mcp_cfg, self._k8s)
        checks = await asyncio.gather(
            *[self._fga.check(user_id, "can_use", f"mcp_server:{s}") for s in SERVERS]
        )
        tools: list[dict] = []
        for server_id, allowed in zip(SERVERS, checks):
            if not allowed:
                continue
            if server_id == "git":
                cfg = await asyncio.to_thread(self._mcp_cfg.get_git_config)
                has_config = bool(cfg.get("repositories"))
            else:
                instances = await asyncio.to_thread(self._mcp_cfg.list_instances, server_id)
                has_config = bool(instances)
            if not has_config:
                continue
            tools.extend(client.get_tools_for_server(server_id))
        return tools, client

    async def _condense_history(
        self,
        history: list[dict],
        ollama_url: str,
        fallback_url: str,
        fallback_model: str,
        fallback_headers: dict,
    ) -> list[dict]:
        """Return a condensed version of history when it exceeds _SUMMARIZE_AFTER messages.

        The oldest messages (beyond the _KEEP_RECENT window) are replaced by a single
        system message containing a LLM-generated summary.
        """
        if len(history) <= _SUMMARIZE_AFTER:
            return history

        to_summarize = history[:-_KEEP_RECENT]
        recent       = history[-_KEEP_RECENT:]

        summary = await self._summarize_messages(
            to_summarize, ollama_url, fallback_url, fallback_model, fallback_headers
        )
        if not summary:
            # Summarization failed — fall back to sending only the recent window
            return recent

        summary_msg = {
            "role": "system",
            "content": f"Summary of earlier conversation:\n{summary}",
        }
        return [summary_msg, *recent]

    async def _pick_summary_endpoint(
        self, ollama_url: str, fallback_url: str, fallback_model: str, fallback_headers: dict
    ) -> tuple[str, str, dict]:
        """Return (url, model, headers) for the summarization call.

        Prefers any Ollama model already loaded in RAM (local, free).
        Falls back to whatever model/endpoint the chat is already using.
        """
        try:
            async with httpx.AsyncClient() as http:
                r = await http.get(f"{ollama_url}/api/ps", timeout=5.0)
            if r.is_success:
                running = r.json().get("models", [])
                if running:
                    return f"{ollama_url}/v1/chat/completions", running[0]["name"], {}
        except Exception:
            pass
        return fallback_url, fallback_model, fallback_headers

    async def _summarize_messages(
        self,
        messages: list[dict],
        ollama_url: str,
        fallback_url: str,
        fallback_model: str,
        fallback_headers: dict,
    ) -> str:
        """Ask the LLM to summarize a list of messages. Returns empty string on failure."""
        url, model, headers = await self._pick_summary_endpoint(
            ollama_url, fallback_url, fallback_model, fallback_headers
        )

        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in messages
            if m["role"] in ("user", "assistant")
        )
        prompt = [
            {
                "role": "user",
                "content": (
                    "Summarize the following conversation concisely. "
                    "Preserve all key facts, decisions, code snippets and context "
                    "that would be needed to continue the conversation naturally. "
                    "Reply only with the summary, no preamble.\n\n"
                    + transcript
                ),
            }
        ]
        payload = {"model": model, "messages": prompt, "stream": False, "temperature": 0.0}

        try:
            async with httpx.AsyncClient() as http:
                r = await http.post(url, json=payload, headers=headers, timeout=60.0)
            if r.is_success:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
        return ""

    async def compact_messages(
        self,
        messages: list[dict],
        existing_summary: str,
        ollama_url: str,
        fallback_url: str,
        fallback_model: str,
        fallback_headers: dict,
    ) -> str:
        """Summarize messages, incorporating the existing_summary for continuity.

        Returns the new rolling summary, or empty string on failure.
        """
        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in messages
            if m["role"] in ("user", "assistant")
        )
        context_part = f"Previous summary:\n{existing_summary}\n\n" if existing_summary else ""
        prompt_content = (
            f"{context_part}"
            f"New messages to incorporate:\n{transcript}\n\n"
            "Generate an updated concise summary of the entire conversation. "
            "Preserve all key facts, decisions, code snippets and context that would be needed "
            "to continue the conversation naturally. Reply only with the summary, no preamble."
        )
        url, model, headers = await self._pick_summary_endpoint(
            ollama_url, fallback_url, fallback_model, fallback_headers
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt_content}],
            "stream": False,
            "temperature": 0.0,
        }
        try:
            async with httpx.AsyncClient() as http:
                r = await http.post(url, json=payload, headers=headers, timeout=60.0)
            if r.is_success:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            pass
        return ""

    async def _run_tool(self, tool_call: dict, client: MCPClient, user_id: str) -> str:
        fn = tool_call.get("function", {})
        name: str = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = {}

        server_id = name.split("__")[0] if "__" in name else ""
        result = await client.call(server_id, name, args)
        await self._audit.log(user_id, "tool_call", name, {"server": server_id})
        return result
