"""Unified model catalogue — LiteLLM configured + Ollama local/running.
Also: LLM model registry and access management.
"""

import asyncio

from connectors.k8s import K8s
from connectors.litellm import LiteLLM
from connectors.ollama import Ollama
from connectors.openfga import SHOKAN_OBJECT, OpenFGA


class Models:
    """Aggregates model lists from LiteLLM and Ollama.

    Instantiate once per application startup; all methods are async.
    """

    def __init__(self) -> None:
        self.litellm = LiteLLM()
        self.ollama = Ollama()
        self._k8s = K8s()

    def _system_models(self) -> set[str]:
        """Return the set of system/embedding model base names to exclude from chat."""
        try:
            names = self._k8s.list_system_models()
        except Exception:
            names = ["nomic-embed-text"]
        return {n.split(":")[0] for n in names}

    async def available(self) -> list[str]:
        """Return models usable right now.

        Cloud models registered in LiteLLM are always included.
        Ollama-backed models (underlying model starts with "ollama/") are
        included only if currently loaded in RAM.
        Running Ollama models not yet registered in LiteLLM are appended too.
        System/embedding models (nomic-embed-text etc.) are never included.
        """
        model_infos, running_names = await asyncio.gather(
            self.litellm.list_model_info(),
            self.ollama_running(),
        )
        running_set = {r["name"] for r in running_names} if running_names and isinstance(running_names[0], dict) else set(running_names)
        system = await asyncio.to_thread(self._system_models)
        seen: set[str] = set()
        result: list[str] = []

        for m in model_infos:
            alias = m.get("model_name", "")
            underlying = m.get("litellm_params", {}).get("model", "")
            if not alias:
                continue
            if underlying.lower().startswith("ollama/"):
                ollama_name = underlying[len("ollama/"):]
                if ollama_name not in running_set:
                    continue
                if ollama_name.split(":")[0] in system:
                    continue
            if alias not in seen:
                result.append(alias)
                seen.add(alias)

        # Append running Ollama models not yet wired into LiteLLM, excluding system models
        for name in running_set:
            base = name.split(":")[0]
            if base in system:
                continue
            candidate = f"ollama/{name}"
            if candidate not in seen and name not in seen:
                result.append(candidate)
                seen.add(candidate)

        return result

    async def litellm_configured(self) -> list[str]:
        """Return model aliases currently registered in LiteLLM."""
        return await self.litellm.list_models()

    async def litellm_registered_set(self) -> set[str]:
        """Return currently registered LiteLLM model aliases as a set."""
        return set(await self.litellm_configured())

    async def ollama_local(self) -> list[str]:
        """Return names of all models installed in Ollama."""
        models = await self.ollama.list_local()
        return [m["name"] for m in models]

    async def ollama_running(self) -> list[str]:
        """Return names of Ollama models currently loaded in RAM."""
        models = await self.ollama.running_models()
        return [m["name"] for m in models]

    async def would_leave_no_models(
        self,
        exclude_litellm_alias: str | None = None,
        exclude_ollama_running: str | None = None,
    ) -> bool:
        """Return True if the given removal/unload would leave no available models.

        exclude_litellm_alias: LiteLLM model_name alias being deleted.
        exclude_ollama_running: Ollama model name being unloaded or deleted.
        """
        model_infos, running = await asyncio.gather(
            self.litellm.list_model_info(),
            self.ollama.running_models(),
        )
        running_set = {r["name"] for r in running}
        if exclude_ollama_running:
            running_set.discard(exclude_ollama_running)

        seen: set[str] = set()
        for m in model_infos:
            alias = m.get("model_name", "")
            underlying = m.get("litellm_params", {}).get("model", "")
            if not alias or alias == exclude_litellm_alias:
                continue
            if underlying.lower().startswith("ollama/"):
                if underlying[len("ollama/"):] not in running_set:
                    continue
            seen.add(alias)

        for name in running_set:
            if f"ollama/{name}" not in seen and name not in seen:
                seen.add(f"ollama/{name}")

        return len(seen) == 0

    async def installed_chat_models(self) -> list[str]:
        """Return all selectable models for preference/profile UI.

        Includes all installed Ollama models (not just running) and LiteLLM cloud
        models, excluding system/embedding models.  Used for picking a preferred
        model — availability at chat time is checked separately.
        """
        model_infos, local, system = await asyncio.gather(
            self.litellm.list_model_info(),
            self.ollama.list_local(),
            asyncio.to_thread(self._system_models),
        )
        seen: set[str] = set()
        result: list[str] = []

        for m in model_infos:
            alias = m.get("model_name", "")
            underlying = m.get("litellm_params", {}).get("model", "")
            if not alias:
                continue
            if underlying.lower().startswith("ollama/"):
                continue  # surfaced via local list below
            if alias not in seen:
                result.append(alias)
                seen.add(alias)

        for m in local:
            name = m.get("name", "")
            base = name.split(":")[0]
            if base in system:
                continue
            candidate = f"ollama/{name}"
            if candidate not in seen and name not in seen:
                result.append(candidate)
                seen.add(candidate)

        return result

    async def ollama_fits_hardware(self) -> list[str]:
        """Return names of locally installed Ollama models that fit cluster RAM."""
        models, ram_gb = await asyncio.gather(
            self.ollama.list_local(),
            asyncio.to_thread(self.ollama.cluster_allocatable_ram_gb),
        )
        return [
            m["name"]
            for m in models
            if self.ollama.fits_in_tenant(m.get("size_gb"), available_ram_gb=ram_gb)[0]
        ]


# ── LLMModels ─────────────────────────────────────────────────────────────────


class LLMModels:
    """Manage LLM model access control via OpenFGA.

    FGA objects: llm_model:<model_id>
    A model must be registered (structural FGA tuple written) before
    per-user access can be granted.
    """

    def __init__(self, litellm: LiteLLM, fga: OpenFGA) -> None:
        self.litellm = litellm
        self.fga = fga

    async def list_from_proxy(self) -> list[str]:
        """Return model IDs currently exposed by LiteLLM."""
        return await self.litellm.list_models()

    async def list_registered(self) -> list[str]:
        """Return model IDs explicitly registered in Shokan (linked via OpenFGA)."""
        tuples = await self.fga.read_tuples_by_user(SHOKAN_OBJECT, "shokan")
        return [
            t["key"]["object"].removeprefix("llm_model:")
            for t in tuples
            if t.get("key", {}).get("object", "").startswith("llm_model:")
        ]

    async def register(self, model_id: str) -> None:
        """Link a model to the platform so admins and members can call it."""
        writes = [
            {"user": SHOKAN_OBJECT, "relation": "shokan", "object": f"llm_model:{model_id}"},
            # Grant all platform members can_call without requiring per-user grants
            {"user": f"{SHOKAN_OBJECT}#member", "relation": "allowed_user", "object": f"llm_model:{model_id}"},
        ]
        try:
            await self.fga.write(writes=writes)
        except Exception:
            # Fall back if FGA schema doesn't support userset subjects
            await self.fga.write(writes=writes[:1])

    async def unregister(self, model_id: str) -> None:
        """Remove all FGA tuples for a model (structural + per-user grants)."""
        try:
            tuples = await self.fga.read_tuples(f"llm_model:{model_id}")
            if tuples:
                to_delete = [
                    {"user": t["key"]["user"], "relation": t["key"]["relation"], "object": t["key"]["object"]}
                    for t in tuples
                ]
                await self.fga.write(deletes=to_delete)
                return
        except Exception:
            pass
        await self.fga.remove_relation(SHOKAN_OBJECT, "shokan", f"llm_model:{model_id}")

    async def get_model_access(self, model_id: str) -> dict[str, str]:
        """Return {user_ref: relation} for llm_model:<model_id>."""
        return await self.fga.get_object_tuples(f"llm_model:{model_id}")

    async def grant_access(self, model_id: str, subject: str) -> None:
        await self.fga.write(
            writes=[{"user": subject, "relation": "allowed_user", "object": f"llm_model:{model_id}"}]
        )

    async def revoke_access(self, model_id: str, subject: str) -> None:
        await self.fga.remove_relation(subject, "allowed_user", f"llm_model:{model_id}")
