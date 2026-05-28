"""LiteLLM configuration storage — K8s-backed provider and router settings."""

import asyncio

from connectors.k8s import K8s
from connectors.litellm import LiteLLM
from connectors.openfga import OpenFGA

# ── Provider catalogue ─────────────────────────────────────────────────────────

PROVIDERS: dict[str, dict] = {
    "openai": {
        "label": "OpenAI", "icon": "smart_toy", "color": "green",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "o1-mini", "o3-mini"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True, "placeholder": "sk-…"}],
        "prefix": "",
    },
    "anthropic": {
        "label": "Anthropic", "icon": "psychology", "color": "orange",
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True, "placeholder": "sk-ant-…"}],
        "prefix": "anthropic/",
    },
    "azure": {
        "label": "Azure OpenAI", "icon": "cloud", "color": "blue",
        "models": [],
        "fields": [
            {"key": "api_key", "label": "API Key", "secret": True},
            {"key": "api_base", "label": "Endpoint", "placeholder": "https://<name>.openai.azure.com"},
            {"key": "api_version", "label": "API Version", "placeholder": "2024-02-01"},
            {"key": "deployment", "label": "Deployment name (model alias)", "placeholder": "gpt-4o-prod"},
        ],
        "prefix": "azure/",
    },
    "gemini": {
        "label": "Google Gemini", "icon": "auto_awesome", "color": "yellow",
        "models": ["gemini/gemini-2.0-flash", "gemini/gemini-1.5-pro", "gemini/gemini-1.5-flash"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "bedrock": {
        "label": "AWS Bedrock", "icon": "cloud_queue", "color": "amber",
        "models": [
            "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0",
            "bedrock/meta.llama3-70b-instruct-v1:0",
            "bedrock/amazon.nova-pro-v1:0",
        ],
        "fields": [
            {"key": "api_key", "label": "Access Key ID", "secret": True},
            {"key": "aws_secret_key", "label": "Secret Access Key", "secret": True},
            {"key": "api_base", "label": "Region", "placeholder": "us-east-1"},
        ],
        "prefix": "",
    },
    "xai": {
        "label": "xAI (Grok)", "icon": "insights", "color": "grey",
        "models": ["xai/grok-2", "xai/grok-beta", "xai/grok-vision-beta"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "groq": {
        "label": "Groq", "icon": "speed", "color": "red",
        "models": ["groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant", "groq/mixtral-8x7b-32768", "groq/gemma2-9b-it"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "together": {
        "label": "Together AI", "icon": "group_work", "color": "purple",
        "models": [
            "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1",
            "together_ai/deepseek-ai/DeepSeek-R1",
        ],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "deepseek": {
        "label": "DeepSeek", "icon": "explore", "color": "indigo",
        "models": ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "mistral": {
        "label": "Mistral AI", "icon": "air", "color": "cyan",
        "models": ["mistral/mistral-large-latest", "mistral/mistral-small-latest", "mistral/codestral-latest", "mistral/pixtral-large-latest"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "perplexity": {
        "label": "Perplexity AI", "icon": "search", "color": "teal",
        "models": ["perplexity/sonar-pro", "perplexity/sonar", "perplexity/sonar-reasoning-pro"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "cohere": {
        "label": "Cohere", "icon": "psychology_alt", "color": "pink",
        "models": ["command-r-plus-08-2024", "command-r-08-2024", "command-light"],
        "fields": [{"key": "api_key", "label": "API Key", "secret": True}],
        "prefix": "",
    },
    "huggingface": {
        "label": "HuggingFace Endpoints", "icon": "hub", "color": "orange",
        "models": [],
        "fields": [
            {"key": "api_key", "label": "API Token", "secret": True},
            {"key": "api_base", "label": "Inference Endpoint URL", "placeholder": "https://…huggingface.cloud"},
            {"key": "model_id", "label": "Model ID / alias", "placeholder": "my-llama"},
        ],
        "prefix": "huggingface/",
    },
}

ROUTING_STRATEGIES = {
    "simple-shuffle": "Simple shuffle",
    "least-busy": "Least busy",
    "latency-based-routing": "Lowest latency",
    "cost-based-routing": "Lowest cost",
    "usage-based-routing": "Lowest usage",
}


class LiteLLMConfig:
    """Read/write LiteLLM configuration blobs stored in the K8s Secret."""

    def __init__(self, k8s: K8s) -> None:
        self.k8s = k8s

    # ── Provider config ────────────────────────────────────────────────────────

    def read_provider(self, provider_id: str) -> dict:
        return self.k8s.read_json(f"litellm-provider-{provider_id}")

    def write_provider(self, provider_id: str, cfg: dict) -> None:
        self.k8s.write_json(f"litellm-provider-{provider_id}", cfg)

    def read_secret(self, provider_id: str, field: str) -> str:
        return self.k8s.read(f"litellm-secret-{provider_id}-{field}")

    def write_secret(self, provider_id: str, field: str, value: str) -> None:
        self.k8s.write(f"litellm-secret-{provider_id}-{field}", value)

    # ── Router config ──────────────────────────────────────────────────────────

    def read_router(self) -> dict:
        return self.k8s.read_json("litellm-router-config")

    def write_router(self, cfg: dict) -> None:
        self.k8s.write_json("litellm-router-config", cfg)

    # ── MCP tools config ───────────────────────────────────────────────────────

    def read_mcp(self) -> dict:
        return self.k8s.read_json("litellm-mcp-config")

    def write_mcp(self, enabled_servers: list[str]) -> None:
        self.k8s.write_json("litellm-mcp-config", {"enabled_servers": enabled_servers})

    # ── A2A / Agents config ────────────────────────────────────────────────────

    def read_a2a(self) -> dict:
        return self.k8s.read_json("litellm-a2a-config")

    def write_a2a(self, cfg: dict) -> None:
        self.k8s.write_json("litellm-a2a-config", cfg)


async def sync_configured_models(litellm: LiteLLM, cfg: "LiteLLMConfig", fga: OpenFGA) -> int:
    """Re-register all configured cloud models in LiteLLM; deregister removed ones.

    Skips models already present. Returns the number of models pushed.
    Called on startup and from the manual force-sync button.
    """
    from services.models import LLMModels

    llm_models = LLMModels(litellm, fga)

    try:
        existing = set(await litellm.list_models())
    except Exception:
        existing = set()

    currently_active: set[str] = set()
    count = 0
    for pid, meta in PROVIDERS.items():
        try:
            provider_cfg = await asyncio.to_thread(cfg.read_provider, pid)
            active = provider_cfg.get("active_models", [])
            currently_active.update(active)
            if not active:
                continue
            api_key = await asyncio.to_thread(cfg.read_secret, pid, "api_key") or None
            prefix = meta.get("prefix", "")
            for alias in active:
                if alias in existing:
                    continue
                full = next((x for x in meta["models"] if x.split("/")[-1] == alias), alias)
                full_model = f"{prefix}{full}" if prefix and not full.startswith(prefix) else full
                try:
                    await litellm.add_model(
                        model_name=alias,
                        model=full_model,
                        api_key=api_key,
                        api_base=provider_cfg.get("api_base") or None,
                        api_version=provider_cfg.get("api_version") or None,
                    )
                    await llm_models.register(alias)
                    count += 1
                    print(f"[model-sync] {alias} → {full_model}", flush=True)
                except Exception as exc:
                    print(f"[model-sync] failed {alias}: {exc}", flush=True)
        except Exception as exc:
            print(f"[model-sync] error reading provider {pid}: {exc}", flush=True)

    # Remove cloud models that were previously registered but are no longer configured
    try:
        registered = set(await llm_models.list_registered())
        stale = {a for a in registered - currently_active if not a.startswith("ollama/")}
        if stale:
            infos = await litellm.list_model_info()
            alias_to_id = {m.get("model_name"): m.get("model_info", {}).get("id", "") for m in infos}
            for alias in stale:
                try:
                    mid = alias_to_id.get(alias, "")
                    if mid:
                        await litellm.remove_model(mid)
                    await llm_models.unregister(alias)
                    print(f"[model-sync] removed stale model: {alias}", flush=True)
                except Exception as exc:
                    print(f"[model-sync] failed to remove {alias}: {exc}", flush=True)
    except Exception:
        pass

    return count
