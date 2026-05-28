"""LiteLLM proxy API client."""

import os

import httpx


class LiteLLM:
    """Client for the LiteLLM proxy — model listing and runtime management.

    Reads LITELLM_URL and LITELLM_MASTER_KEY from env.
    """

    def __init__(self) -> None:
        self.url = os.getenv("LITELLM_URL", "http://litellm:8000")
        self._headers = {"Authorization": f"Bearer {os.getenv('LITELLM_MASTER_KEY', '')}"}

    # ── Model management ───────────────────────────────────────────────────────

    async def list_models(self) -> list[str]:
        """Return model_name aliases currently registered."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/models", headers=self._headers, timeout=5.0)
                if r.is_success:
                    return [m["id"] for m in r.json().get("data", [])]
            except Exception:
                pass
        return []

    async def list_model_info(self) -> list[dict]:
        """Return full model records including internal id (needed for deletion)."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/model/info", headers=self._headers, timeout=5.0)
                if r.is_success:
                    return r.json().get("data", [])
            except Exception:
                pass
        return []

    async def add_model(
        self,
        model_name: str,
        model: str,
        api_key: str | None = None,
        api_base: str | None = None,
        api_version: str | None = None,
        **extra_params,
    ) -> None:
        """Register a model in LiteLLM at runtime.

        model_name : alias exposed by the proxy (e.g. "gpt-4o", "llama3")
        model      : litellm model string (e.g. "gpt-4o", "ollama/llama3")
        """
        params: dict = {"model": model}
        if api_key:
            params["api_key"] = api_key
        if api_base:
            params["api_base"] = api_base
        if api_version:
            params["api_version"] = api_version
        params.update(extra_params)

        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"{self.url}/model/new",
                headers=self._headers,
                json={"model_name": model_name, "litellm_params": params},
                timeout=10.0,
            )
            r.raise_for_status()

    async def remove_model(self, model_id: str) -> None:
        """Remove a model by its internal LiteLLM id."""
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"{self.url}/model/delete",
                headers=self._headers,
                json={"id": model_id},
                timeout=10.0,
            )
            r.raise_for_status()

    # ── Config updates ─────────────────────────────────────────────────────────

    async def update_config(self, payload: dict) -> None:
        """POST /config/update with an arbitrary settings dict."""
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{self.url}/config/update",
                headers=self._headers,
                json=payload,
                timeout=10.0,
            )

    async def get_config(self) -> dict:
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/config", headers=self._headers, timeout=5.0)
                if r.is_success:
                    return r.json()
            except Exception:
                pass
        return {}

    # ── Version ────────────────────────────────────────────────────────────────

    async def get_version(self) -> str:
        """Return the running LiteLLM version from /health, or '' on failure."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/health", headers=self._headers, timeout=5.0)
                if r.is_success:
                    return r.json().get("litellm_version", "")
            except Exception:
                pass
        return ""

    async def get_latest_pypi_version(self) -> str:
        """Return the latest published LiteLLM version from PyPI, or '' on failure."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get("https://pypi.org/pypi/litellm/json", timeout=10.0)
                if r.is_success:
                    return r.json()["info"]["version"]
            except Exception:
                pass
        return ""

    async def get_spend_logs(self, limit: int = 500) -> list[dict]:
        """Return per-request spend logs from LiteLLM (requires DB spend tracking).

        Returns empty list if spend tracking is not enabled.
        """
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(
                    f"{self.url}/global/spend/logs",
                    headers=self._headers,
                    params={"limit": limit},
                    timeout=10.0,
                )
                if r.is_success:
                    data = r.json()
                    if isinstance(data, list):
                        return data
                    return data.get("data", [])
            except Exception:
                pass
        return []

    async def get_global_spend(self) -> dict:
        """Return global spend summary {total_cost, spend_by_model, ...} or {} on failure."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(
                    f"{self.url}/global/spend",
                    headers=self._headers,
                    timeout=10.0,
                )
                if r.is_success:
                    return r.json()
            except Exception:
                pass
        return {}
