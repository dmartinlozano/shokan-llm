"""Shokan built-in agents — configuration persistence and execution."""

import asyncio
import json

import httpx
from qdrant_client import QdrantClient

from config import OLLAMA_URL as _OLLAMA_URL
from connectors.k8s import K8s
from connectors.rag import QDRANT_URL, scroll_qdrant_index

AGENT_IDS = ["rag_curator", "cronjob_monitor", "investigator", "onboarding"]

AGENT_META: dict[str, dict] = {
    "rag_curator": {
        "label":       "RAG Curator",
        "icon":        "auto_fix_high",
        "description": "Scans the vector index, detects imbalances and oversized files, and suggests improvements.",
    },
    "cronjob_monitor": {
        "label":       "CronJob Monitor",
        "icon":        "monitor_heart",
        "description": "Checks Kubernetes CronJob health, flags overdue or suspended jobs, and generates a status summary.",
    },
    "investigator": {
        "label":       "Investigator",
        "icon":        "manage_search",
        "description": "Goal-driven agent: plans an investigation, uses connected MCP tools to gather data, and delivers a report.",
    },
    "onboarding": {
        "label":       "Onboarding",
        "icon":        "waving_hand",
        "description": "Generates a personalized welcome message for a new user based on their model access and connected tools.",
    },
}

_DEFAULTS: dict[str, dict] = {
    "rag_curator": {
        "enabled":              True,
        "model":                "",
        "min_chunks_warning":   100,
        "report_lang":          "English",
    },
    "cronjob_monitor": {
        "enabled":                   True,
        "model":                     "",
        "overdue_threshold_minutes": 60,
        "report_lang":               "English",
    },
    "investigator": {
        "enabled":         True,
        "model":           "",
        "max_rounds":      5,
        "allowed_servers": [],
    },
    "onboarding": {
        "enabled":      True,
        "model":        "",
        "language":     "English",
        "extra_notes":  "",
    },
}

_K8S_KEYS: dict[str, str] = {
    "rag_curator":     "agent-rag-curator-config",
    "cronjob_monitor": "agent-cronjob-monitor-config",
    "investigator":    "agent-investigator-config",
    "onboarding":      "agent-onboarding-config",
}


class AgentStore:
    """Read/write per-agent configs from the K8s secret."""

    def __init__(self, k8s: K8s) -> None:
        self._k8s = k8s

    def read(self, agent_id: str) -> dict:
        stored = self._k8s.read_json(_K8S_KEYS[agent_id])
        return {**_DEFAULTS.get(agent_id, {}), **stored}

    def write(self, agent_id: str, cfg: dict) -> None:
        self._k8s.write_json(_K8S_KEYS[agent_id], cfg)


async def _llm_complete(
    url: str,
    model: str,
    headers: dict,
    messages: list[dict],
    timeout: float = 90.0,
) -> str:
    payload = {"model": model, "messages": messages, "stream": False, "temperature": 0.0}
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(url, json=payload, headers=headers, timeout=timeout)
        if r.is_success:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return ""


async def resolve_llm(
    preferred_model: str,
    ollama_url: str,
    litellm_url: str,
    litellm_headers: dict,
) -> tuple[str, str, dict]:
    """Return (chat_url, model, headers). Prefer preferred_model; auto-detect if blank.

    Auto-detect priority:
      1. Any Ollama model already loaded in RAM (free, local, zero-latency spin-up).
      2. First model registered in LiteLLM (cloud or proxied).
    """
    if preferred_model:
        if preferred_model.startswith("ollama/"):
            return f"{ollama_url}/v1/chat/completions", preferred_model[len("ollama/"):], {}
        return f"{litellm_url}/chat/completions", preferred_model, litellm_headers

    # 1. Prefer a running Ollama model (no spin-up cost)
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(f"{ollama_url}/api/ps", timeout=5.0)
        if r.is_success:
            running = r.json().get("models", [])
            if running:
                return f"{ollama_url}/v1/chat/completions", running[0]["name"], {}
    except Exception:
        pass

    # 2. Fall back to the first LiteLLM-registered model
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(f"{litellm_url}/models", headers=litellm_headers, timeout=5.0)
        if r.is_success:
            models = r.json().get("data", [])
            if models:
                return f"{litellm_url}/chat/completions", models[0]["id"], litellm_headers
    except Exception:
        pass

    return f"{litellm_url}/chat/completions", "", litellm_headers


# ── Agent runners ──────────────────────────────────────────────────────────────

class RagCuratorAgent:
    """Analyzes the RAG vector index and returns an LLM-generated health report."""

    async def run(
        self,
        cfg: dict,
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str = _OLLAMA_URL,
    ) -> str:
        url, model, headers = await resolve_llm(cfg.get("model", ""), ollama_url, litellm_url, litellm_headers)
        if not model:
            return "⚠️ No LLM model available. Install an Ollama model or configure a cloud provider first."

        try:
            qdrant  = QdrantClient(url=QDRANT_URL)
            entries = await asyncio.to_thread(scroll_qdrant_index, qdrant)
        except Exception as exc:
            return f"⚠️ Could not reach Qdrant: {exc}"

        if not entries:
            return "The RAG index is empty — no documents have been ingested yet."

        total_chunks = sum(e["chunks"] for e in entries)
        by_ds: dict[str, int] = {}
        for e in entries:
            by_ds[e["datasource_id"]] = by_ds.get(e["datasource_id"], 0) + e["chunks"]

        min_warn = int(cfg.get("min_chunks_warning", 100))
        lang     = cfg.get("report_lang", "English")

        lines = [
            f"Total indexed files: {len(entries)}",
            f"Total chunks: {total_chunks}",
            "Chunks by datasource:",
        ]
        for ds, cnt in sorted(by_ds.items(), key=lambda x: -x[1]):
            lines.append(f"  - {ds or '(unknown)'}: {cnt} chunks")

        large = [e for e in entries if e["chunks"] > min_warn]
        if large:
            lines.append(f"\nFiles exceeding {min_warn} chunks (potentially oversized):")
            for e in large[:10]:
                lines.append(f"  - {e['file_path']} ({e['chunks']} chunks) [{e['datasource_id']}]")

        prompt = (
            f"You are a RAG knowledge base curator. Analyze the index statistics below and write "
            f"a concise health report in {lang}. Flag issues such as oversized files, imbalanced "
            f"datasources, or missing content types, and suggest concrete improvements. "
            f"Be brief and actionable.\n\n" + "\n".join(lines)
        )
        result = await _llm_complete(url, model, headers, [{"role": "user", "content": prompt}])
        return result or "LLM did not return a response."


class CronJobMonitorAgent:
    """Checks Kubernetes CronJob health and returns an LLM-generated summary."""

    async def run(
        self,
        cfg: dict,
        k8s: K8s,
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str = _OLLAMA_URL,
    ) -> str:
        url, model, headers = await resolve_llm(cfg.get("model", ""), ollama_url, litellm_url, litellm_headers)
        if not model:
            return "⚠️ No LLM model available."

        try:
            jobs = await asyncio.to_thread(k8s.list_cronjobs)
        except Exception as exc:
            return f"⚠️ Could not list CronJobs: {exc}"

        if not jobs:
            return "No CronJobs found in the namespace."

        threshold_secs = int(cfg.get("overdue_threshold_minutes", 60)) * 60
        lang           = cfg.get("report_lang", "English")

        lines = ["CronJob status:"]
        for j in jobs:
            age_min = j["age_secs"] // 60 if j["age_secs"] else 0
            ok_min  = j["last_ok_secs"] // 60 if j.get("last_ok_secs") is not None else None
            status  = "SUSPENDED" if j["suspended"] else "active"
            overdue = bool(j["age_secs"]) and j["age_secs"] > threshold_secs
            lines.append(
                f"  - {j['k8s_name']} [{status}] schedule={j['schedule']} "
                f"last_run={age_min}m ago "
                + (f"last_ok={ok_min}m ago" if ok_min is not None else "last_ok=never")
                + (" ⚠️ OVERDUE" if overdue else "")
            )

        prompt = (
            f"You are a Kubernetes operations assistant. Analyze the CronJob statuses below and write "
            f"a concise health summary in {lang}. Flag suspended jobs, overdue runs, and jobs that have "
            f"never succeeded. Be brief and actionable.\n\n" + "\n".join(lines)
        )
        result = await _llm_complete(url, model, headers, [{"role": "user", "content": prompt}])
        return result or "LLM did not return a response."


class InvestigatorAgent:
    """Goal-driven agent: plans an investigation and executes it via MCP tools."""

    async def run(
        self,
        goal: str,
        cfg: dict,
        user_id: str,
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str = _OLLAMA_URL,
    ) -> str:
        from connectors.mcp import MCP, SERVERS
        from connectors.mcp_client import MCPClient
        from connectors.openfga import OpenFGA

        url, model, headers = await resolve_llm(cfg.get("model", ""), ollama_url, litellm_url, litellm_headers)
        if not model:
            return "⚠️ No LLM model available."

        max_rounds       = int(cfg.get("max_rounds", 5))
        allowed_servers: list[str] = cfg.get("allowed_servers") or list(SERVERS)

        k8s     = K8s()
        fga     = OpenFGA()
        mcp_cfg = MCP(k8s, fga)
        client  = MCPClient(mcp_cfg, k8s)

        checks = await asyncio.gather(
            *[fga.check(user_id, "can_use", f"mcp_server:{s}") for s in allowed_servers]
        )
        tools: list[dict] = []
        for sid, ok in zip(allowed_servers, checks):
            if not ok:
                continue
            if sid == "git":
                git_cfg = await asyncio.to_thread(mcp_cfg.get_git_config)
                if not git_cfg.get("repositories"):
                    continue
            else:
                instances = await asyncio.to_thread(mcp_cfg.list_instances, sid)
                if not instances:
                    continue
            tools.extend(client.get_tools_for_server(sid))

        messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    "You are an investigator agent. Given a goal, plan your steps, use the available "
                    "tools to gather information, and deliver a structured markdown report. "
                    "Do not ask clarifying questions — act on what you have."
                ),
            },
            {"role": "user", "content": goal},
        ]

        for _ in range(max_rounds):
            payload: dict = {"model": model, "messages": messages, "stream": False, "temperature": 0.0}
            if tools:
                payload["tools"]       = tools
                payload["tool_choice"] = "auto"

            async with httpx.AsyncClient() as http:
                r = await http.post(url, json=payload, headers=headers, timeout=120.0)
            if not r.is_success:
                return f"[Error {r.status_code}] {r.text[:300]}"

            choice     = (r.json().get("choices") or [{}])[0]
            message    = choice.get("message", {})
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                return message.get("content", "").strip() or "Agent produced no output."

            messages.append(message)
            results = await asyncio.gather(*[_call_tool(tc, client) for tc in tool_calls])
            for tc, result in zip(tool_calls, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        # Reached round limit — force final synthesis
        messages.append({"role": "user", "content": "Summarize your findings into a final report."})
        async with httpx.AsyncClient() as http:
            r = await http.post(
                url, json={"model": model, "messages": messages, "stream": False},
                headers=headers, timeout=120.0,
            )
        if r.is_success:
            return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return "Agent reached the maximum number of rounds without a conclusion."


async def _call_tool(tool_call: dict, client) -> str:
    fn   = tool_call.get("function", {})
    name = fn.get("name", "")
    try:
        args = json.loads(fn.get("arguments", "{}"))
    except Exception:
        args = {}
    server_id = name.split("__")[0] if "__" in name else ""
    return await client.call(server_id, name, args)


class OnboardingAgent:
    """Generates a personalized onboarding message for a user."""

    async def run(
        self,
        user_id: str,
        username: str,
        cfg: dict,
        litellm_url: str,
        litellm_headers: dict,
        ollama_url: str = _OLLAMA_URL,
    ) -> str:
        from connectors.mcp import SERVERS
        from connectors.openfga import OpenFGA, SHOKAN_OBJECT
        from services.models import Models

        url, model, headers = await resolve_llm(cfg.get("model", ""), ollama_url, litellm_url, litellm_headers)
        if not model:
            return "⚠️ No LLM model available."

        fga        = OpenFGA()
        models_svc = Models()
        lang       = cfg.get("language", "English")
        notes      = cfg.get("extra_notes", "")

        installed, is_admin = await asyncio.gather(
            models_svc.installed_chat_models(),
            fga.check(user_id, "can_manage_services", SHOKAN_OBJECT),
        )

        model_checks = await asyncio.gather(
            *[fga.check(user_id, "can_call", f"llm_model:{m}") for m in installed]
        )
        accessible_models = [m for m, ok in zip(installed, model_checks) if ok] or installed

        server_checks = await asyncio.gather(
            *[fga.check(user_id, "can_use", f"mcp_server:{s}") for s in SERVERS]
        )
        accessible_servers = [s for s, ok in zip(SERVERS, server_checks) if ok]

        context = (
            f"User: {username} ({'admin' if is_admin else 'member'})\n"
            f"Available AI models: {', '.join(accessible_models) if accessible_models else 'none configured'}\n"
            f"Connected data tools (MCP): {', '.join(accessible_servers) if accessible_servers else 'none'}\n"
        )
        if notes:
            context += f"Additional context: {notes}\n"

        prompt = (
            f"Write a warm, concise onboarding message in {lang} for a new user of Shokan-LLM, "
            "an enterprise AI platform. Use the user profile below to personalize it: explain which "
            "AI models they can use, what data tools are connected, and give 2-3 practical starter tips. "
            "Be friendly and use markdown formatting.\n\n" + context
        )
        result = await _llm_complete(url, model, headers, [{"role": "user", "content": prompt}])
        return result or "LLM did not return a response."
