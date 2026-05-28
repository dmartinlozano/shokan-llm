"""
Shokan-LLM LiteLLM + Ollama settings — merged page class.

Imported by main.py and rendered at /litellm-settings.
No entrypoint, no auth routes, no standalone startup.
Requires a NiceGUI @ui.page context to call render().
"""

import asyncio
import json
import re

import httpx
from nicegui import ui

from config import DEFAULT_MODEL
from templates.crud_template import CRUDTemplate
from templates.dialogs import last_model_warning
from connectors.k8s import K8s
from connectors.litellm import LiteLLM
from connectors.ollama import Ollama
from connectors.openfga import OpenFGA
from services.agents import (
    AGENT_IDS, AGENT_META, AgentStore,
    RagCuratorAgent, CronJobMonitorAgent, InvestigatorAgent, OnboardingAgent,
)
from services.audit import AuditLog
from services.skills import SkillsStorage
from services.litellm_config import LiteLLMConfig, PROVIDERS, ROUTING_STRATEGIES
from services.models import Models, LLMModels
from services.permissions import can


class OllamaView:
    """Renders the Ollama model management page with 3 tabs."""

    def __init__(self) -> None:
        self.ollama = Ollama()
        self.litellm = LiteLLM()
        self.models = Models()
        self.llm_models = LLMModels(self.litellm, OpenFGA())
        self.k8s = K8s()

    async def render(self, user: dict, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()

        reachable = await self.ollama.is_reachable()
        if not reachable:
            with ui.column().classes("items-center gap-3 p-8"):
                ui.icon("cloud_off", size="xl").classes("text-gray-400")
                ui.label("Ollama unavailable").classes("text-lg font-semibold text-gray-500")
                ui.label(f"Cannot connect to {self.ollama.url}").classes("text-sm text-gray-400")
            return

        # Pre-fetch cluster RAM and system models in threads to avoid blocking the event loop.
        cached_ram_gb, system_models_list = await asyncio.gather(
            asyncio.to_thread(self.ollama.cluster_allocatable_ram_gb),
            asyncio.to_thread(self.k8s.list_system_models),
        )

        # Mutable cell so _render_installed_tab can share its refresh fn with _render_browse_tab
        # without storing per-request state on self (which is a shared singleton).
        installed_refresh: list = [None]

        with ui.tabs().classes("w-full bg-gray-100") as tabs:
            tab_browse = ui.tab("Browse models", icon="search")
            tab_installed = ui.tab("Manage models", icon="inventory_2")

        with ui.tab_panels(tabs, value=tab_browse).classes("w-full"):
            with ui.tab_panel(tab_browse):
                await self._render_browse_tab(perms, cached_ram_gb, installed_refresh)
            with ui.tab_panel(tab_installed):
                self._render_installed_tab(perms, system_models=system_models_list, installed_refresh=installed_refresh, cached_ram_gb=cached_ram_gb)

    # ── Browse ─────────────────────────────────────────────────────────────────

    async def _render_browse_tab(self, perms: set[str] | None = None, cached_ram_gb=None, installed_refresh: list | None = None) -> None:
        if perms is None:
            perms = set()
        ui.label("Find models with AI").classes("text-lg font-semibold mb-1")
        ui.label(
            "Describe what you need and the assistant will suggest Ollama-compatible models. "
            "The indicator shows whether the model fits the cluster resources."
        ).classes("text-sm text-gray-500 mb-3")

        desc_input = (
            ui.textarea(placeholder="e.g. 'a small fast model for code generation that fits in 4 GB'…")
            .classes("w-full")
            .props("outlined dense rows=3")
        )
        results_box = ui.column().classes("w-full gap-3 mt-4")

        async def find_models() -> None:
            description = desc_input.value.strip()
            if not description:
                ui.notify("Please describe what you need.", type="warning")
                return
            results_box.clear()
            with results_box:
                ui.spinner(size="lg").classes("mx-auto mt-8")

            default_model = DEFAULT_MODEL
            system_prompt = (
                "You are a model recommendation assistant for Ollama. "
                "The user describes their requirements. "
                "Respond with ONLY a valid JSON array of model name strings available on Ollama's model library, "
                'using the format "name:tag" (e.g. ["llama3.2:3b", "mistral:7b", "gemma2:2b"]). '
                "Suggest 5 to 8 models. No explanation, no markdown, just the JSON array."
            )

            suggested: list[str] = []
            try:
                async with httpx.AsyncClient() as http:
                    r = await http.post(
                        f"{self.litellm.url}/chat/completions",
                        json={
                            "model": default_model,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": description},
                            ],
                        },
                        headers=self.litellm._headers,
                        timeout=30.0,
                    )
                if r.is_success:
                    content = r.json()["choices"][0]["message"]["content"].strip()
                    if content.startswith("```"):
                        parts = content.split("```")
                        content = parts[1] if len(parts) > 1 else content
                        if content.startswith("json"):
                            content = content[4:]
                    suggested = json.loads(content)
                else:
                    results_box.clear()
                    with results_box:
                        ui.label(f"Model error ({r.status_code}).").classes("text-red-500 text-sm")
                    return
            except Exception as exc:
                results_box.clear()
                with results_box:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-sm")
                return

            results_box.clear()
            if not isinstance(suggested, list) or not suggested:
                with results_box:
                    ui.label("No suggestions returned.").classes("text-gray-400 text-sm italic mt-4")
                return
            with results_box:
                for model_name in suggested:
                    if isinstance(model_name, str):
                        self._suggestion_card(model_name, can_download, cached_ram_gb=cached_ram_gb, installed_refresh=installed_refresh)

        can_download = not perms or "models:ollama:create" in perms
        ui.button("Find models", icon="auto_awesome", on_click=find_models).props("unelevated").classes("mt-2")

    def _suggestion_card(self, model_name: str, can_download: bool = True, cached_ram_gb: float | None = None, installed_refresh: list | None = None) -> None:
        size_gb = _estimate_size_gb(model_name)
        fits, reason = self.ollama.fits_in_tenant(size_gb, available_ram_gb=cached_ram_gb)
        semaphore_color = "green" if fits else "red"
        semaphore_icon = "check_circle" if fits else "cancel"

        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-3"):
                ui.icon(semaphore_icon, size="sm").classes(f"text-{semaphore_color}-500 shrink-0")
                with ui.column().classes("flex-1 gap-0"):
                    ui.label(model_name).classes("font-semibold text-sm font-mono")
                    size_label = f"~{size_gb:.1f} GB estimated · {reason}" if size_gb else reason
                    ui.label(size_label).classes("text-xs text-gray-400")
                if can_download:
                    ui.button(
                        "Download",
                        icon="download",
                        on_click=self._make_pull_handler(model_name, None, installed_refresh),
                    ).props("unelevated dense").classes("text-xs")

    def _make_pull_handler(self, pull_id: str, selected_variant: dict | None, installed_refresh: list | None = None):
        async def handler() -> None:
            full_id = pull_id
            if selected_variant and selected_variant.get("value"):
                full_id = f"{pull_id}:{selected_variant['value']}"
            ui.notify(f"Downloading {full_id}… this may take several minutes.", type="info", timeout=6000)
            refresh_fn = installed_refresh[0] if installed_refresh else None
            asyncio.create_task(
                _pull_background(self.ollama, full_id, self.llm_models, refresh_fn)
            )

        return handler

    # ── Manage models ──────────────────────────────────────────────────────────

    def _render_installed_tab(self, perms: set[str] | None = None, system_models: list | None = None, installed_refresh: list | None = None, cached_ram_gb: float | None = None) -> None:
        if perms is None:
            perms = set()
        can_load_unload = not perms or "models:ollama:start" in perms
        can_delete      = not perms or "models:ollama:delete" in perms

        system_models = set(system_models) if system_models else {"nomic-embed-text"}

        def _is_system(full_name: str) -> bool:
            base = full_name.split(":")[0]
            return base in system_models or full_name in system_models

        ram_gb = cached_ram_gb or 0.0
        if ram_gb:
            ui.label(f"Total cluster allocatable RAM: {ram_gb} GB").classes("text-sm text-gray-400 mb-3")

        # ── Table ─────────────────────────────────────────────────────────────
        _COLS = 6  # Name | Size | Status | VRAM | Expires | Actions
        with ui.row().classes("w-full justify-between items-center mb-2"):
            ui.label("Manage models").classes("text-lg font-semibold text-slate-800")
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
            with ui.grid(columns=_COLS).classes(
                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
            ):
                for col in ["NAME", "SIZE", "STATUS", "VRAM", "EXPIRES", ""]:
                    ui.label(col).classes("font-semibold text-slate-500 text-xs tracking-wider")
            rows_box = ui.column().classes("w-full")

        def load_rows():
            asyncio.ensure_future(_async_load())

        async def _async_load():
            try:
                installed, running = await asyncio.gather(
                    self.ollama.list_local(),
                    self.ollama.running_models(),
                )
            except Exception as exc:
                rows_box.clear()
                with rows_box:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-sm px-4 py-3")
                return

            running_map = {r["name"]: r for r in running}
            rows_box.clear()

            if not installed:
                with rows_box:
                    ui.label("No models installed.").classes("text-slate-400 text-sm italic px-4 py-3")
                return

            for m in installed:
                full   = m.get("name", "")
                in_ram = full in running_map
                locked = _is_system(full)
                with rows_box:
                    with ui.grid(columns=_COLS).classes(
                        "w-full items-center px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                    ):
                        with ui.row().classes("items-center gap-1"):
                            if locked:
                                ui.icon("lock", size="xs").classes("text-slate-400 shrink-0").tooltip("System model")
                            ui.label(_short_model_name(full)).classes("font-mono text-sm text-slate-700 truncate")
                        ui.label(f"{m.get('size_gb', 0):.2f} GB").classes("text-sm text-slate-600")
                        with ui.row().classes("items-center gap-1"):
                            ui.icon("circle", size="xs").classes(
                                "text-green-500" if in_ram else "text-slate-300"
                            )
                            ui.label("Running" if in_ram else "Ready").classes("text-sm text-slate-600")
                        rv = running_map.get(full, {})
                        ui.label(
                            f"{round(rv.get('size_vram', 0) / 1024**3, 2)} GB" if in_ram else "—"
                        ).classes("text-sm text-slate-500")
                        ui.label(
                            (rv.get("expires_at", "") or "")[:19].replace("T", " ") or "—"
                            if in_ram else "—"
                        ).classes("text-sm text-slate-500")

                        with ui.row().classes("items-center justify-end gap-1"):
                            if locked:
                                ui.icon("lock", size="sm").classes("text-slate-300 mx-1").tooltip(
                                    "System model — cannot be stopped or deleted"
                                )
                            else:
                                if can_load_unload:
                                    icon  = "stop"         if in_ram else "play_arrow"
                                    color = "color=negative" if in_ram else "color=positive"
                                    tip   = "Unload from RAM" if in_ram else "Load into RAM"

                                    async def on_toggle(f=full, r=in_ram):
                                        await _do_toggle(f, r)

                                    ui.button(
                                        icon=icon,
                                        on_click=on_toggle,
                                    ).props(f"flat round dense {color}").tooltip(tip)
                                if can_delete:
                                    async def on_delete(f=full):
                                        await _do_delete(f)

                                    ui.button(
                                        icon="delete",
                                        on_click=on_delete,
                                    ).props("flat round dense color=negative").tooltip("Delete")

        if installed_refresh is not None:
            installed_refresh[0] = _async_load

        async def _do_toggle(full: str, in_ram: bool):
            name = _short_model_name(full)
            if not in_ram:
                ui.notify(f"Loading '{name}'…", type="info", timeout=10000)
                try:
                    await self.ollama.load(full)
                except Exception as exc:
                    ui.notify(f"Failed to load '{name}': {exc}", type="negative", timeout=8000)
                    return
                ui.notify(f"'{name}' loaded.", type="positive")
                await _async_load()
                return
            is_last = await self.models.would_leave_no_models(exclude_ollama_running=full)
            if not is_last:
                try:
                    await self.ollama.unload(full)
                except Exception as exc:
                    ui.notify(f"Failed to unload '{name}': {exc}", type="negative", timeout=8000)
                    return
                ui.notify(f"'{name}' unloaded.", type="info")
                await _async_load()
                return

            async def do_unload():
                await self.ollama.unload(full)
                ui.notify(f"'{name}' unloaded.", type="info")
                await _async_load()

            last_model_warning(do_unload)

        def _delete(full: str):
            asyncio.ensure_future(_do_delete(full))

        async def _do_delete(full: str):
            name = _short_model_name(full)
            is_last = await self.models.would_leave_no_models(exclude_ollama_running=full)

            async def do_delete():
                await self.ollama.delete(full)
                # Clean up any LiteLLM registration and FGA tuple for this Ollama model
                infos = await self.litellm.list_model_info()
                ollama_alias = f"ollama/{full}"
                for m in infos:
                    underlying = m.get("litellm_params", {}).get("model", "")
                    if underlying in (ollama_alias, full):
                        mid = m.get("model_info", {}).get("id", "")
                        alias = m.get("model_name", "")
                        if mid:
                            try:
                                await self.litellm.remove_model(mid)
                                await self.llm_models.unregister(alias)
                            except Exception:
                                pass
                ui.notify(f"'{name}' deleted.", type="info")
                await _async_load()

            if not is_last:
                await do_delete()
                return
            last_model_warning(do_delete)

        async def do_refresh():
            await _async_load()

        refresh_btn.on("click", do_refresh)
        load_rows()


# ── Ollama helpers ────────────────────────────────────────────────────────────


def _short_model_name(name: str) -> str:
    """Return a compact display name from a full Ollama model identifier."""
    if name.startswith("hf.co/"):
        tag = name.split(":")[-1] if ":" in name else name.split("/")[-1]
        tag = re.sub(r"\.gguf$", "", tag, flags=re.IGNORECASE)
        return tag
    return name


def _estimate_size_gb(model_name: str) -> float | None:
    """Rough size estimate (GB) from the model tag, e.g. 'llama3.2:3b' → ~1.8 GB."""
    tag = model_name.split(":")[-1].lower()
    m = re.search(r"(\d+(?:\.\d+)?)b", tag)
    if not m:
        return None
    params_b = float(m.group(1))
    return round(params_b * 0.6, 1)


async def _pull_background(ollama: Ollama, name: str, llm_models=None, on_success=None) -> None:
    try:
        await ollama.pull(name)
        ui.notify(f"'{name}' downloaded successfully.", type="positive")
        if llm_models is not None:
            try:
                await llm_models.register(f"ollama/{name}")
            except Exception:
                pass
        if on_success is not None:
            try:
                await on_success()
            except Exception:
                pass
    except Exception as exc:
        ui.notify(f"Error downloading '{name}': {exc}", type="negative", timeout=8000)


# ── LitellmView ───────────────────────────────────────────────────────────────


class LitellmView:
    """Renders the LiteLLM configuration page."""

    def __init__(self) -> None:
        self.litellm = LiteLLM()
        self.k8s = K8s()
        self.cfg = LiteLLMConfig(self.k8s)
        self.models = Models()
        self.llm_models = LLMModels(self.litellm, OpenFGA())
        self.ollama_settings = OllamaView()
        self.agent_store = AgentStore(self.k8s)
        self.audit = AuditLog()

    async def render(self, user: dict, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()

        with ui.tabs().classes("w-full bg-gray-100") as tabs:
            tab_cloud  = ui.tab("Cloud Models", icon="key")       if can(perms, "models:cloud:read")   else None
            tab_ollama = ui.tab("Ollama", icon="smart_toy")       if can(perms, "models:ollama:read")  else None
            tab_router = ui.tab("Routing", icon="alt_route")      if can(perms, "models:routing:read") else None
            tab_a2a    = ui.tab("A2A / Agents", icon="share")     if can(perms, "models:a2a:read")     else None
            tab_agents = ui.tab("Agents", icon="psychology")      if can(perms, "models:agents:read")  else None
            tab_skills = ui.tab("Skills", icon="auto_awesome")    if can(perms, "models:skills:read")  else None
            tab_usage  = ui.tab("Usage", icon="analytics")

        first_tab = next(
            (t for t in [tab_cloud, tab_ollama, tab_router, tab_a2a, tab_agents, tab_skills, tab_usage] if t is not None),
            None,
        )
        if first_tab is None:
            ui.label("No model configuration tabs available.").classes("text-gray-500 text-sm p-4")
            return

        with ui.tab_panels(tabs, value=first_tab).classes("w-full") as self._tab_panels:
            if tab_cloud:
                with ui.tab_panel(tab_cloud):
                    await self._render_cloud_models_tab(perms)
            if tab_ollama:
                with ui.tab_panel(tab_ollama):
                    await self.ollama_settings.render({}, perms)
            if tab_router:
                with ui.tab_panel(tab_router):
                    await self._render_router_tab(perms)
            if tab_a2a:
                with ui.tab_panel(tab_a2a):
                    await self._render_a2a_tab(perms)
            if tab_agents:
                with ui.tab_panel(tab_agents):
                    await self._render_agents_tab(perms, user)
            if tab_skills:
                with ui.tab_panel(tab_skills):
                    self._render_skills_tab(perms)
            with ui.tab_panel(tab_usage):
                await self._render_usage_tab()

    # ── Tab: Cloud Models (sub-tabs) ───────────────────────────────────────────

    async def _render_cloud_models_tab(self, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()
        with ui.tabs().classes("w-full bg-gray-50 border-b border-gray-200") as sub_tabs:
            sub_active        = ui.tab("Active",    icon="check_circle")
            sub_tab_available = ui.tab("Available", icon="key")

        with ui.tab_panels(sub_tabs, value=sub_active).classes("w-full") as sub_tab_panels:
            with ui.tab_panel(sub_active):
                await self._render_models_tab(perms, sub_tab_panels, sub_tab_available)
            with ui.tab_panel(sub_tab_available):
                await self._render_providers_tab(perms)

    # ── Sub-tab: Available providers (table) ───────────────────────────────────

    async def _render_providers_tab(self, perms: set[str] | None = None) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        if perms is None:
            perms = set()
        can_save = can(perms, "models:cloud:update")

        with ui.row().classes("w-full items-center justify-between mb-1"):
            ui.label("Cloud Models").classes("text-lg font-semibold")
            if can_save:
                async def force_sync():
                    from services.litellm_config import sync_configured_models
                    btn.props("loading")
                    try:
                        n = await sync_configured_models(self.litellm, self.cfg, self.llm_models.fga)
                        ui.notify(
                            f"Re-synced {n} model(s)." if n else "All models already in sync.",
                            type="positive" if n else "info",
                        )
                    except Exception as exc:
                        ui.notify(f"Sync failed: {exc}", type="negative")
                    finally:
                        btn.props(remove="loading")
                btn = ui.button(icon="sync", on_click=force_sync).props("flat round dense").tooltip(
                    "Force re-sync all models to LiteLLM (use after LiteLLM restart)"
                )

        ui.label(
            "Configure credentials and register models. Ollama models are managed in the Ollama tab."
        ).classes("text-sm text-gray-500 mb-4")

        try:
            registered = await self.models.litellm_registered_set()
        except Exception:
            registered = set()

        # Table header
        with ui.row().classes("w-full text-xs font-semibold text-gray-400 uppercase px-3 pb-2 border-b gap-3"):
            ui.label("Provider").classes("w-44 shrink-0")
            ui.label("API Key").classes("w-56 shrink-0")
            ui.label("Models to register").classes("flex-1")
            ui.label("").classes("w-20 shrink-0")

        for pid, meta in PROVIDERS.items():
            provider_cfg = await asyncio.to_thread(self.cfg.read_provider, pid)
            has_key = bool(await asyncio.to_thread(self.cfg.read_secret, pid, "api_key"))
            self._provider_row(pid, meta, registered, can_save, provider_cfg=provider_cfg, has_key=has_key)

    def _provider_row(self, provider_id: str, meta: dict, registered: set[str], can_save: bool = True, provider_cfg: dict | None = None, has_key: bool = False) -> None:
        if provider_cfg is None:
            provider_cfg = {}
        active_models = set(provider_cfg.get("active_models", []))
        model_aliases = [m.split("/")[-1] for m in meta["models"]]
        selected = list(active_models & set(model_aliases)) or [a for a in model_aliases if a in registered]
        has_extra = any(f["key"] != "api_key" for f in meta["fields"])

        with ui.row().classes("w-full items-center px-3 py-2 hover:bg-gray-50 rounded gap-3 border-b border-gray-100"):
            # Provider
            with ui.row().classes("w-44 shrink-0 items-center gap-2"):
                ui.icon(meta["icon"], size="xs").classes(f"text-{meta['color']}-500")
                ui.label(meta["label"]).classes("text-sm font-medium truncate")

            # API Key
            key_input = ui.input(
                placeholder="••••••••" if has_key else "API Key",
                password=True,
            ).classes("w-56 shrink-0").props("outlined dense")

            # Models combo
            if model_aliases:
                models_select = ui.select(
                    model_aliases,
                    multiple=True,
                    value=selected,
                ).classes("flex-1").props("outlined dense use-chips")
            else:
                models_select = None
                ui.label("Configure via ⚙").classes("flex-1 text-xs text-gray-400 italic px-2")

            # Actions
            with ui.row().classes("w-20 shrink-0 gap-0 items-center"):
                async def do_save(pid=provider_id, ki=key_input, ms=models_select, m=meta):
                    if ki.value:
                        await asyncio.to_thread(self.cfg.write_secret, pid, "api_key", ki.value)
                        ki.value = ""
                    api_key = await asyncio.to_thread(self.cfg.read_secret, pid, "api_key")
                    saved = await asyncio.to_thread(self.cfg.read_provider, pid)
                    active = []
                    failed = []
                    aliases = (ms.value if isinstance(ms.value, list) else ([ms.value] if ms.value else [])) if ms else []
                    for alias in aliases:
                        full = next((x for x in m["models"] if x.split("/")[-1] == alias), alias)
                        pfx = m["prefix"]
                        full_model = f"{pfx}{full}" if pfx and not full.startswith(pfx) else full
                        try:
                            await self.litellm.add_model(
                                model_name=alias,
                                model=full_model,
                                api_key=api_key or None,
                                api_base=saved.get("api_base") or None,
                                api_version=saved.get("api_version") or None,
                            )
                            await self.llm_models.register(alias)
                            active.append(alias)
                        except Exception as exc:
                            failed.append(alias)
                    saved["active_models"] = active
                    await asyncio.to_thread(self.cfg.write_provider, pid, saved)
                    if failed:
                        ui.notify(
                            f"{m['label']}: could not register {', '.join(failed)} — is LiteLLM running?",
                            type="negative", timeout=8000,
                        )
                    else:
                        ui.notify(f"{m['label']} saved.", type="positive")

                if can_save:
                    ui.button(icon="save", on_click=do_save).props("flat round dense").classes("text-blue-500").tooltip("Save")

                if has_extra:
                    async def open_adv(pid=provider_id, m=meta):
                        await self._open_advanced_dialog(pid, m)
                    ui.button(icon="settings", on_click=open_adv).props("flat round dense").classes("text-gray-500").tooltip("Advanced settings")

    async def _open_advanced_dialog(self, provider_id: str, meta: dict) -> None:
        saved = await asyncio.to_thread(self.cfg.read_provider, provider_id)

        with ui.dialog() as dlg, ui.card().classes("w-96"):
            with ui.column().classes("w-full gap-3"):
                with ui.row().classes("items-center gap-2 mb-1"):
                    ui.icon(meta["icon"], size="sm").classes(f"text-{meta['color']}-500")
                    ui.label(f"{meta['label']} — Advanced").classes("font-semibold")

                inputs: dict = {}
                for f in meta["fields"]:
                    if f["key"] == "api_key":
                        continue
                    if f.get("secret"):
                        has_val = bool(await asyncio.to_thread(self.cfg.read_secret, provider_id, f["key"]))
                        inp = ui.input(
                            f["label"],
                            placeholder="••••••••" if has_val else f.get("placeholder", ""),
                            password=True,
                        ).classes("w-full").props("outlined dense")
                    else:
                        inp = ui.input(
                            f["label"],
                            value=saved.get(f["key"], ""),
                            placeholder=f.get("placeholder", ""),
                        ).classes("w-full").props("outlined dense")
                    inputs[f["key"]] = inp

                alias_inp = None
                if not meta["models"]:
                    ui.separator().classes("my-1")
                    ui.label("Register a model").classes("text-xs font-semibold text-gray-500")
                    alias_inp = ui.input("Alias", placeholder="my-model").classes("w-full").props("outlined dense")
                    existing = saved.get("active_models", [])
                    if existing:
                        with ui.column().classes("gap-0 mt-1"):
                            ui.label("Currently registered:").classes("text-xs text-gray-400")
                            for a in existing:
                                ui.label(f"• {a}").classes("text-xs font-mono text-gray-600")

                async def save_adv(pid=provider_id, m=meta, inps=inputs, ai=alias_inp):
                    new_cfg = dict(saved)
                    for key, inp in inps.items():
                        f_meta = next((f for f in m["fields"] if f["key"] == key), {})
                        if f_meta.get("secret"):
                            if inp.value:
                                await asyncio.to_thread(self.cfg.write_secret, pid, key, inp.value)
                                inp.value = ""
                        else:
                            new_cfg[key] = inp.value
                    if ai and ai.value.strip():
                        alias = ai.value.strip()
                        api_key = await asyncio.to_thread(self.cfg.read_secret, pid, "api_key")
                        pfx = m["prefix"]
                        full_model = f"{pfx}{alias}"
                        try:
                            await self.litellm.add_model(
                                model_name=alias, model=full_model,
                                api_key=api_key or None,
                                api_base=new_cfg.get("api_base") or None,
                                api_version=new_cfg.get("api_version") or None,
                            )
                            await self.llm_models.register(alias)
                            active = new_cfg.get("active_models", [])
                            if alias not in active:
                                active.append(alias)
                            new_cfg["active_models"] = active
                        except Exception:
                            pass
                    await asyncio.to_thread(self.cfg.write_provider, pid, new_cfg)
                    ui.notify(f"{m['label']} saved.", type="positive")
                    dlg.close()

                with ui.row().classes("gap-2 justify-end w-full mt-2"):
                    ui.button("Cancel", on_click=dlg.close).props("flat dense")
                    ui.button("Save", icon="save", on_click=save_adv).props("unelevated dense")
        dlg.open()

    # ── Tab: Active models (CRUDTemplate) ──────────────────────────────────────

    async def _render_models_tab(self, perms: set[str] | None = None, sub_tab_panels=None, sub_tab_available=None) -> None:
        if perms is None:
            perms = set()
        can_delete = can(perms, "models:cloud:delete")
        async def refresh_data():
            models = await self.litellm.list_model_info()
            return [
                {
                    "alias": m.get("model_name", "—"),
                    "model": m.get("litellm_params", {}).get("model", "—"),
                    "_id": m.get("model_info", {}).get("id", ""),
                }
                for m in models
            ]

        crud: list[CRUDTemplate] = []

        async def on_delete(item: dict):
            is_last = await self.models.would_leave_no_models(
                exclude_litellm_alias=item.get("alias")
            )

            async def _do_remove():
                await self.litellm.remove_model(item["_id"])
                await self.llm_models.unregister(item.get("alias", ""))
                ui.notify("Model removed.", type="info")
                if crud:
                    crud[0].refresh()

            if not is_last:
                await _do_remove()
                return
            last_model_warning(_do_remove)

        def go_to_providers():
            if sub_tab_panels and sub_tab_available:
                sub_tab_panels.set_value(sub_tab_available)

        tpl = CRUDTemplate(
            title="Active models",
            columns=["Alias", "Model"],
            on_refresh=refresh_data,
            on_new_click=go_to_providers,
            on_delete=on_delete if can_delete else None,
        )
        crud.append(tpl)

    async def _render_version_card(self) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        with ui.card().classes("w-full"):
            with ui.row().classes("items-center gap-4 flex-wrap w-full"):
                ui.icon("info", size="sm").classes("text-blue-400")
                current_lbl = ui.label("Checking version…").classes("text-sm font-mono text-gray-700")
                latest_lbl = ui.label("").classes("text-sm")
                update_btn = ui.button("Update to latest", icon="system_update").props("unelevated dense").classes("ml-auto hidden")

        current, latest = await asyncio.gather(
            self.litellm.get_version(),
            self.litellm.get_latest_pypi_version(),
        )

        if current:
            current_lbl.text = f"Running: v{current}"
        else:
            current_lbl.text = "Version: unknown"

        if latest and current:
            if current == latest:
                latest_lbl.text = "Up to date"
                latest_lbl.classes("text-green-600 font-medium")
            else:
                latest_lbl.text = f"Latest: v{latest}"
                latest_lbl.classes("text-amber-600 font-medium")
                update_btn.classes(remove="hidden")

                async def do_upgrade(v=latest) -> None:
                    with page_slot:
                        with ui.dialog() as dlg, ui.card():
                            ui.label(f"Upgrade model gateway to v{v}?").classes("font-semibold mb-2")
                            ui.label("The gateway pod will restart. Active requests will be interrupted.").classes("text-sm text-gray-500 mb-4")
                            with ui.row().classes("gap-2 justify-end w-full"):
                                ui.button("Cancel", on_click=dlg.close).props("flat")

                                async def confirm_upgrade(ver=v, d=dlg) -> None:
                                    d.close()
                                    try:
                                        await asyncio.to_thread(self.cfg.k8s.upgrade_deployment_image, "litellm", f"ghcr.io/berriai/litellm:main-v{ver}")
                                        ui.notify(f"Model gateway upgrading to v{ver} — rollout started.", type="positive")
                                        current_lbl.text = f"Upgrading to v{ver}…"
                                        latest_lbl.text = ""
                                        update_btn.classes("hidden")
                                    except Exception as exc:
                                        ui.notify(f"Upgrade failed: {exc}", type="negative")

                                ui.button("Upgrade", icon="system_update", on_click=confirm_upgrade).props("unelevated").classes("bg-blue-500 text-white")
                    dlg.open()

                update_btn.on_click(do_upgrade)
        elif latest:
            latest_lbl.text = f"Latest available: v{latest}"
            latest_lbl.classes("text-gray-500 text-sm")

    # ── Tab: Routing ───────────────────────────────────────────────────────────

    async def _render_router_tab(self, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()
        can_save = can(perms, "models:routing:update")
        ui.label("Router configuration").classes("text-lg font-semibold mb-1")
        ui.label("Load-balancing strategy, retries, and fallbacks between models.").classes("text-sm text-gray-500 mb-4")

        router_cfg = await asyncio.to_thread(self.cfg.read_router)

        with ui.card().classes("w-full mb-3"):
            ui.label("General").classes("font-medium mb-3")
            with ui.grid(columns=2).classes("w-full gap-4"):
                strategy = ui.select(ROUTING_STRATEGIES, value=router_cfg.get("routing_strategy", "simple-shuffle"), label="Strategy").classes("w-full")
                retries = ui.number("Retries", value=router_cfg.get("num_retries", 2), min=0, max=10).classes("w-full")
                timeout = ui.number("Timeout (s)", value=router_cfg.get("timeout", 120), min=5, max=600).classes("w-full")
                cooldown = ui.number("Cooldown after failure (s)", value=router_cfg.get("cooldown_time", 60), min=0, max=300).classes("w-full")

        with ui.card().classes("w-full mb-3"):
            ui.label("Fallbacks").classes("font-medium mb-2")
            ui.label("If the primary model fails, the gateway tries the following in order.").classes("text-xs text-gray-400 mb-2")
            fallbacks_box = ui.column().classes("w-full gap-1")
            fallbacks: list[dict] = list(router_cfg.get("fallbacks", []))

            def refresh_fallbacks() -> None:
                fallbacks_box.clear()
                with fallbacks_box:
                    for i, fb in enumerate(fallbacks):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(fb.get("model", "")).classes("font-mono text-sm flex-1")
                            ui.label("→").classes("text-gray-400")
                            ui.label(", ".join(fb.get("fallbacks", []))).classes("text-sm text-gray-500 flex-1")
                            ui.button(icon="delete", on_click=lambda idx=i: (fallbacks.pop(idx), refresh_fallbacks())).props("flat round dense").classes("text-red-400")

            refresh_fallbacks()

            with ui.row().classes("gap-2 items-end mt-2 flex-wrap"):
                fb_model = ui.input("Primary model", placeholder="gpt-4o").classes("w-40")
                fb_targets = ui.input("Fallbacks (comma-separated)", placeholder="claude-3-5-sonnet,llama3").classes("flex-1")

                def add_fallback() -> None:
                    if not fb_model.value:
                        return
                    fallbacks.append({"model": fb_model.value, "fallbacks": [t.strip() for t in fb_targets.value.split(",") if t.strip()]})
                    fb_model.value = fb_targets.value = ""
                    refresh_fallbacks()

                ui.button("Add", icon="add", on_click=add_fallback).props("flat dense")

        async def save_router() -> None:
            new_cfg = {
                "routing_strategy": strategy.value,
                "num_retries": int(retries.value),
                "timeout": int(timeout.value),
                "cooldown_time": int(cooldown.value),
                "fallbacks": fallbacks,
            }
            await asyncio.to_thread(self.cfg.write_router, new_cfg)
            try:
                await self.litellm.update_config({"router_settings": new_cfg})
            except Exception:
                pass
            ui.notify("Routing saved.", type="positive")

        if can_save:
            ui.button("Save", icon="save", on_click=save_router).props("unelevated dense")

    # ── Tab: A2A / Agents ──────────────────────────────────────────────────────

    async def _render_a2a_tab(self, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()
        can_save = can(perms, "models:a2a:update")
        ui.label("A2A — Agent to Agent Protocol").classes("text-lg font-semibold mb-1")
        ui.label("Configure the Agent Card for this tenant (Google A2A protocol).").classes("text-sm text-gray-500 mb-4")

        a2a_cfg = await asyncio.to_thread(self.cfg.read_a2a)

        with ui.card().classes("w-full mb-3"):
            ui.label("Agent Card").classes("font-medium mb-3")
            name = ui.input("Agent name", value=a2a_cfg.get("name", "Shokan AI")).classes("w-full")
            desc = ui.textarea("Description", value=a2a_cfg.get("description", "Shokan-LLM agentic AI platform")).classes("w-full").props("rows=3")
            url = ui.input("Public agent URL", value=a2a_cfg.get("url", ""), placeholder="https://shokan.example.com").classes("w-full")
            version = ui.input("Version", value=a2a_cfg.get("version", "1.0.0")).classes("w-48")

        with ui.card().classes("w-full mb-3"):
            ui.label("Skills").classes("font-medium mb-2")
            skills: list[dict] = list(a2a_cfg.get("skills", []))
            skills_box = ui.column().classes("w-full gap-1")

            def refresh_skills() -> None:
                skills_box.clear()
                with skills_box:
                    for i, sk in enumerate(skills):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(sk.get("id", "")).classes("font-mono text-sm w-32")
                            ui.label(sk.get("name", "")).classes("text-sm flex-1")
                            ui.label(sk.get("description", "")).classes("text-xs text-gray-400 flex-1 truncate")
                            ui.button(icon="delete", on_click=lambda idx=i: (skills.pop(idx), refresh_skills())).props("flat round dense").classes("text-red-400")

            refresh_skills()

            with ui.row().classes("gap-2 items-end mt-2 flex-wrap"):
                sk_id = ui.input("ID", placeholder="text_chat").classes("w-28")
                sk_name = ui.input("Name", placeholder="Text chat").classes("w-40")
                sk_desc = ui.input("Description", placeholder="Answers questions…").classes("flex-1")

                def add_skill() -> None:
                    if not sk_id.value:
                        return
                    skills.append({"id": sk_id.value, "name": sk_name.value, "description": sk_desc.value})
                    sk_id.value = sk_name.value = sk_desc.value = ""
                    refresh_skills()

                ui.button("Add", icon="add", on_click=add_skill).props("flat dense")

        with ui.card().classes("w-full mb-3"):
            ui.label("A2A Authentication").classes("font-medium mb-2")
            ui.label(
                "Protects POST /a2a/tasks. The Agent Card at /.well-known/agent.json is always public."
            ).classes("text-xs text-gray-400 mb-2")
            auth_scheme = ui.select(
                {"none": "No authentication", "bearer": "Bearer Token", "oauth2": "OAuth2 (coming soon)"},
                value=a2a_cfg.get("auth_scheme", "none"),
                label="Scheme",
            ).classes("w-64")

            bearer_note = ui.label(
                "Set the token by running:  kubectl patch secret shokanllm-secret -n shokanllm "
                "--type merge -p '{\"stringData\":{\"a2a-bearer-token\":\"<your-token>\"}}'",
            ).classes("text-xs text-gray-500 font-mono mt-2 whitespace-pre-wrap")
            bearer_note.set_visibility(a2a_cfg.get("auth_scheme", "none") == "bearer")
            auth_scheme.on_value_change(lambda e: bearer_note.set_visibility(e.value == "bearer"))

        async def save_a2a() -> None:
            await asyncio.to_thread(self.cfg.write_a2a, {
                "name": name.value, "description": desc.value, "url": url.value,
                "version": version.value, "skills": skills, "auth_scheme": auth_scheme.value,
            })
            ui.notify("A2A configuration saved.", type="positive")

        if can_save:
            ui.button("Save", icon="save", on_click=save_a2a).props("unelevated dense")

    # ── Tab: Built-in Agents ───────────────────────────────────────────────────

    async def _render_agents_tab(self, perms: set[str] | None = None, user: dict | None = None) -> None:
        if perms is None:
            perms = set()
        if user is None:
            user = {}
        can_update = can(perms, "models:agents:update")

        all_models = await self.models.available()
        model_options = {"": "Auto (prefer running model)"} | {m: m for m in all_models}

        from connectors.mcp import SERVERS as MCP_SERVERS

        cfgs = {
            aid: await asyncio.to_thread(self.agent_store.read, aid)
            for aid in AGENT_IDS
        }

        with ui.tabs().classes("w-full bg-gray-50") as agent_tabs:
            tab_objs = {
                aid: ui.tab(
                    AGENT_META[aid]["label"],
                    icon=AGENT_META[aid]["icon"],
                )
                for aid in AGENT_IDS
            }

        with ui.tab_panels(agent_tabs, value=tab_objs[AGENT_IDS[0]]).classes("w-full"):
            for agent_id in AGENT_IDS:
                meta = AGENT_META[agent_id]
                cfg  = cfgs[agent_id]

                with ui.tab_panel(tab_objs[agent_id]):
                    ui.label(meta["description"]).classes("text-sm text-gray-500 mb-4")

                    with ui.card().classes("w-full"):
                        with ui.row().classes("items-center gap-4 mb-3"):
                            enabled_toggle = ui.switch("Enabled", value=cfg.get("enabled", True))

                        model_sel = ui.select(
                            model_options,
                            value=cfg.get("model", ""),
                            label="Model (optional)",
                        ).classes("w-full mb-3")

                        # ── Agent-specific fields ──────────────────────────────
                        extra_fields: dict = {}

                        if agent_id == "rag_curator":
                            with ui.row().classes("gap-4 flex-wrap"):
                                extra_fields["min_chunks_warning"] = ui.number(
                                    "Warn if file exceeds N chunks",
                                    value=int(cfg.get("min_chunks_warning", 100)),
                                    min=10, step=10,
                                ).classes("w-52")
                                extra_fields["report_lang"] = ui.select(
                                    ["English", "Spanish", "French", "German", "Portuguese"],
                                    value=cfg.get("report_lang", "English"),
                                    label="Report language",
                                ).classes("w-48")

                        elif agent_id == "cronjob_monitor":
                            with ui.row().classes("gap-4 flex-wrap"):
                                extra_fields["overdue_threshold_minutes"] = ui.number(
                                    "Overdue threshold (minutes)",
                                    value=int(cfg.get("overdue_threshold_minutes", 60)),
                                    min=5, step=5,
                                ).classes("w-52")
                                extra_fields["report_lang"] = ui.select(
                                    ["English", "Spanish", "French", "German", "Portuguese"],
                                    value=cfg.get("report_lang", "English"),
                                    label="Report language",
                                ).classes("w-48")

                        elif agent_id == "investigator":
                            with ui.row().classes("gap-4 flex-wrap"):
                                extra_fields["max_rounds"] = ui.number(
                                    "Max tool-calling rounds",
                                    value=int(cfg.get("max_rounds", 5)),
                                    min=1, max=20, step=1,
                                ).classes("w-44")
                            extra_fields["allowed_servers"] = ui.select(
                                {s: s for s in MCP_SERVERS},
                                value=cfg.get("allowed_servers") or list(MCP_SERVERS),
                                label="Allowed MCP servers",
                                multiple=True,
                            ).classes("w-full mt-2").props("use-chips")

                        elif agent_id == "onboarding":
                            with ui.row().classes("gap-4 flex-wrap"):
                                extra_fields["language"] = ui.select(
                                    ["English", "Spanish", "French", "German", "Portuguese"],
                                    value=cfg.get("language", "English"),
                                    label="Message language",
                                ).classes("w-48")
                            extra_fields["extra_notes"] = ui.textarea(
                                "Extra context (optional)",
                                value=cfg.get("extra_notes", ""),
                                placeholder="e.g. 'This is an internal R&D team with focus on ML'",
                            ).classes("w-full mt-2").props("rows=2")

                        # ── Actions ────────────────────────────────────────────
                        ui.separator().classes("my-3")
                        with ui.row().classes("gap-2 items-center"):
                            if can_update:
                                async def _save(aid=agent_id, toggle=enabled_toggle, msel=model_sel, ef=extra_fields) -> None:
                                    new_cfg: dict = {"enabled": toggle.value, "model": msel.value}
                                    for k, widget in ef.items():
                                        new_cfg[k] = widget.value
                                    await asyncio.to_thread(self.agent_store.write, aid, new_cfg)
                                    ui.notify(f"{AGENT_META[aid]['label']} saved.", type="positive")

                                ui.button("Save", icon="save", on_click=_save).props("unelevated dense")

                            if can_update:
                                if agent_id == "rag_curator":
                                    async def _run_rag(msel=model_sel, ef=extra_fields) -> None:
                                        run_cfg = {"model": msel.value, **{k: w.value for k, w in ef.items()}}
                                        with ui.dialog() as dlg, ui.card().classes("w-[640px] p-4 gap-3"):
                                            ui.label("RAG Curator").classes("font-semibold")
                                            result_md = ui.markdown("⏳ Analyzing the index…").classes("text-sm")
                                            dlg.open()
                                            report = await RagCuratorAgent().run(
                                                run_cfg, self.litellm.url, self.litellm._headers
                                            )
                                            result_md.set_content(report)
                                            ui.button("Close", on_click=dlg.close).props("flat dense")
                                    ui.button("Run now", icon="play_arrow", on_click=_run_rag).props("flat dense").classes("text-blue-600")

                                elif agent_id == "cronjob_monitor":
                                    async def _run_cron(msel=model_sel, ef=extra_fields) -> None:
                                        run_cfg = {"model": msel.value, **{k: w.value for k, w in ef.items()}}
                                        with ui.dialog() as dlg, ui.card().classes("w-[640px] p-4 gap-3"):
                                            ui.label("CronJob Monitor").classes("font-semibold")
                                            result_md = ui.markdown("⏳ Checking cluster…").classes("text-sm")
                                            dlg.open()
                                            report = await CronJobMonitorAgent().run(
                                                run_cfg, self.k8s, self.litellm.url, self.litellm._headers
                                            )
                                            result_md.set_content(report)
                                            ui.button("Close", on_click=dlg.close).props("flat dense")
                                    ui.button("Run now", icon="play_arrow", on_click=_run_cron).props("flat dense").classes("text-blue-600")

                                elif agent_id == "investigator":
                                    async def _run_investigator(usr=user, msel=model_sel, ef=extra_fields) -> None:
                                        with ui.dialog() as dlg, ui.card().classes("w-[640px] p-4 gap-3"):
                                            ui.label("Investigator Agent").classes("font-semibold")
                                            goal_input = ui.textarea(
                                                "Investigation goal",
                                                placeholder="e.g. 'Summarize open Jira tickets in the BACKEND project'",
                                            ).classes("w-full").props("rows=3")
                                            result_area = ui.markdown("").classes("text-sm")
                                            spinner = ui.spinner(size="sm").classes("hidden")

                                            async def _go(u=usr, mi=msel, ei=ef, gi=goal_input, ra=result_area, sp=spinner) -> None:
                                                goal = gi.value.strip()
                                                if not goal:
                                                    ui.notify("Enter an investigation goal.", type="warning")
                                                    return
                                                run_cfg = {"model": mi.value, **{k: w.value for k, w in ei.items()}}
                                                sp.classes(remove="hidden")
                                                ra.set_content("")
                                                report = await InvestigatorAgent().run(
                                                    goal, run_cfg, u.get("id", ""),
                                                    self.litellm.url, self.litellm._headers,
                                                )
                                                sp.classes(add="hidden")
                                                ra.set_content(report)

                                            with ui.row().classes("gap-2"):
                                                ui.button("Investigate", icon="search", on_click=_go).props("unelevated dense")
                                                ui.button("Close", on_click=dlg.close).props("flat dense")
                                            dlg.open()
                                    ui.button("Run now", icon="play_arrow", on_click=_run_investigator).props("flat dense").classes("text-blue-600")

                                elif agent_id == "onboarding":
                                    async def _run_onboarding(msel=model_sel, ef=extra_fields) -> None:
                                        with ui.dialog() as dlg, ui.card().classes("w-[640px] p-4 gap-3"):
                                            ui.label("Onboarding Agent").classes("font-semibold")
                                            uid_input   = ui.input("User ID (Keycloak UUID)").classes("w-full")
                                            uname_input = ui.input("Username").classes("w-full")
                                            result_md   = ui.markdown("").classes("text-sm")
                                            spinner     = ui.spinner(size="sm").classes("hidden")

                                            async def _go(mi=msel, ei=ef, ui_=uid_input, un=uname_input, rm=result_md, sp=spinner) -> None:
                                                uid   = ui_.value.strip()
                                                uname = un.value.strip()
                                                if not uid or not uname:
                                                    ui.notify("Enter both user ID and username.", type="warning")
                                                    return
                                                run_cfg = {"model": mi.value, **{k: w.value for k, w in ei.items()}}
                                                sp.classes(remove="hidden")
                                                rm.set_content("")
                                                msg = await OnboardingAgent().run(
                                                    uid, uname, run_cfg, self.litellm.url, self.litellm._headers
                                                )
                                                sp.classes(add="hidden")
                                                rm.set_content(msg)

                                            with ui.row().classes("gap-2"):
                                                ui.button("Generate", icon="waving_hand", on_click=_go).props("unelevated dense")
                                                ui.button("Close", on_click=dlg.close).props("flat dense")
                                            dlg.open()
                                    ui.button("Run now", icon="play_arrow", on_click=_run_onboarding).props("flat dense").classes("text-blue-600")

                            if not can_update:
                                ui.label("Read-only — you don't have permission to configure agents.").classes("text-xs text-gray-400")

    # ── Tab: Skills ────────────────────────────────────────────────────────────

    def _render_skills_tab(self, perms: set[str] | None = None) -> None:
        if perms is None:
            perms = set()
        can_create = can(perms, "models:skills:create")
        can_update = can(perms, "models:skills:update")
        can_delete = can(perms, "models:skills:delete")

        store = SkillsStorage()
        state: dict = {"selected_id": None}

        # Editor widgets — held in a cell so the refreshable list closure can update them
        editor: dict = {
            "name":    None,
            "content": None,
            "enabled": None,
            "save":    None,
        }

        with ui.row().classes("w-full gap-0").style("height: calc(100vh - 160px)"):

            # ── Left: skill list ───────────────────────────────────────────────
            with ui.column().classes("w-56 shrink-0 border-r border-gray-200 h-full overflow-hidden flex flex-col"):

                if can_create:
                    def _new_skill() -> None:
                        sid = store.create_skill()
                        state["selected_id"] = sid
                        _refresh_list.refresh()
                        _load_into_editor(sid)

                    def _restore_default() -> None:
                        from pathlib import Path
                        import re as _re
                        seed = Path(__file__).parent.parent / "skills" / "default.md"
                        if not seed.exists():
                            ui.notify("Seed file not found in image.", type="negative")
                            return
                        text = seed.read_text(encoding="utf-8")
                        fm_re = _re.compile(r"^---\n(.*?)\n---\n?", _re.DOTALL)
                        fm = fm_re.match(text)
                        name = "Shokan Platform Assistant"
                        if fm:
                            for line in fm.group(1).splitlines():
                                if line.startswith("name: "):
                                    name = line[len("name: "):]
                            content = text[fm.end():].strip()
                        else:
                            content = text.strip()
                        sid = store.create_skill(name=name, content=content)
                        state["selected_id"] = sid
                        _refresh_list.refresh()
                        _load_into_editor(sid)
                        ui.notify("Default skill restored.", type="positive")

                    ui.button("New skill", icon="add", on_click=_new_skill).props(
                        "flat align=left"
                    ).classes("w-full text-sm font-medium shrink-0 mt-1 px-2")
                    ui.button("Restore default", icon="restore", on_click=_restore_default).props(
                        "flat align=left"
                    ).classes("w-full text-sm shrink-0 px-2 text-gray-500")
                    ui.separator().classes("shrink-0")

                with ui.column().classes("flex-1 overflow-y-auto gap-0 p-1"):
                    @ui.refreshable
                    def _refresh_list() -> None:
                        skills = store.list_skills()
                        if not skills:
                            ui.label("No skills yet.").classes("text-xs text-gray-400 p-2")
                            return
                        for sk in skills:
                            sid       = sk["id"]
                            is_sel    = sid == state["selected_id"]
                            row_cls   = (
                                "w-full items-center rounded px-2 py-1 cursor-pointer gap-1 group "
                                + ("bg-blue-50" if is_sel else "hover:bg-gray-100")
                            )
                            with ui.row().classes(row_cls).on("click", lambda s=sid: _select(s)):
                                # enable toggle — stop propagation via click.stop
                                tog = ui.switch(value=sk["enabled"]).props("dense")
                                if not can_update:
                                    tog.disable()
                                async def _toggle(e, s=sid) -> None:
                                    store.set_enabled(s, e.value)
                                    _refresh_list.refresh()
                                tog.on("update:model-value", _toggle).on("click.stop", lambda: None)

                                ui.label(sk["name"]).classes(
                                    "flex-1 text-sm truncate "
                                    + ("text-blue-700 font-medium" if is_sel else "text-gray-700")
                                )

                                if can_delete:
                                    async def _del(s=sid) -> None:
                                        store.delete_skill(s)
                                        if state["selected_id"] == s:
                                            state["selected_id"] = None
                                            _clear_editor()
                                        _refresh_list.refresh()
                                    (
                                        ui.button(icon="close")
                                        .props("flat round dense size=xs")
                                        .classes("text-gray-400 hover:text-red-500 shrink-0 opacity-0 group-hover:opacity-100")
                                        .on("click.stop", _del)
                                    )

                    _refresh_list()

            # ── Right: editor ──────────────────────────────────────────────────
            with ui.column().classes("flex-1 p-4 gap-3 overflow-y-auto h-full"):
                ui.label("Select a skill to edit, or create a new one.").classes(
                    "text-sm text-gray-400 italic"
                ).bind_visibility_from(state, "selected_id", lambda v: v is None)

                with ui.column().classes("w-full gap-3").bind_visibility_from(
                    state, "selected_id", lambda v: v is not None
                ):
                    with ui.row().classes("w-full items-center gap-3"):
                        editor["name"] = ui.input("Skill name").classes("flex-1")
                        editor["enabled"] = ui.switch("Active").props("dense")
                        if not can_update:
                            editor["name"].disable()
                            editor["enabled"].disable()

                    editor["content"] = ui.textarea(
                        "Skill content (Markdown)",
                        placeholder=(
                            "Describe the behavior you want to inject.\n\n"
                            "Example:\n"
                            "Always reply in Spanish regardless of the user's language.\n"
                            "When writing code, prefer type hints and follow PEP 8."
                        ),
                    ).classes("w-full font-mono text-sm").props("rows=20 outlined")
                    if not can_update:
                        editor["content"].disable()

                    with ui.row().classes("gap-2 items-center"):
                        if can_update:
                            def _save_skill() -> None:
                                sid = state["selected_id"]
                                if not sid:
                                    return
                                name    = editor["name"].value.strip() or "Unnamed skill"
                                content = editor["content"].value
                                enabled = editor["enabled"].value
                                store.save_skill(sid, name, content, enabled)
                                _refresh_list.refresh()
                                ui.notify("Skill saved.", type="positive")

                            editor["save"] = ui.button(
                                "Save", icon="save", on_click=_save_skill
                            ).props("unelevated dense")

                        if can_delete:
                            def _delete_selected() -> None:
                                sid = state["selected_id"]
                                if not sid:
                                    return
                                skill_name = editor["name"].value or "this skill"

                                with ui.dialog() as dlg, ui.card().classes("p-6 gap-4"):
                                    ui.label(f'Delete "{skill_name}"?').classes("font-semibold text-base")
                                    ui.label("This action cannot be undone.").classes("text-sm text-gray-500")
                                    with ui.row().classes("gap-2 justify-end w-full"):
                                        ui.button("Cancel", on_click=dlg.close).props("flat dense")

                                        def _confirm(s=sid) -> None:
                                            store.delete_skill(s)
                                            state["selected_id"] = None
                                            _clear_editor()
                                            _refresh_list.refresh()
                                            dlg.close()
                                            ui.notify("Skill deleted.", type="warning")

                                        ui.button("Delete", icon="delete", on_click=_confirm).props("unelevated dense").classes("bg-red-500 text-white")
                                dlg.open()

                            ui.button(
                                "Delete", icon="delete", on_click=_delete_selected
                            ).props("flat dense").classes("text-red-500")

        # ── Helpers ────────────────────────────────────────────────────────────

        def _load_into_editor(sid: str) -> None:
            sk = store.load_skill(sid)
            if not sk:
                return
            editor["name"].set_value(sk["name"])
            editor["content"].set_value(sk["content"])
            editor["enabled"].set_value(sk["enabled"])

        def _clear_editor() -> None:
            editor["name"].set_value("")
            editor["content"].set_value("")
            editor["enabled"].set_value(True)

        def _select(sid: str) -> None:
            state["selected_id"] = sid
            _refresh_list.refresh()
            _load_into_editor(sid)

    # ── Tab: Usage ─────────────────────────────────────────────────────────────

    async def _render_usage_tab(self) -> None:
        import datetime
        ui.label("Usage").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label(
            "Token usage and request statistics per model."
        ).classes("text-sm text-gray-500 mb-4")

        with ui.row().classes("w-full justify-end mb-2"):
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        content_area = ui.column().classes("w-full gap-4")

        async def load_usage() -> None:
            content_area.clear()
            with content_area:
                ui.spinner(size="sm").classes("mx-auto my-4")

            # Try LiteLLM spend API first
            spend = await self.litellm.get_global_spend()
            logs  = await self.litellm.get_spend_logs(limit=500)
            # Audit-based fallback stats
            audit_stats = await asyncio.to_thread(self.audit.stats)

            content_area.clear()
            with content_area:
                has_spend = bool(spend) or bool(logs)

                # ── LiteLLM spend section ──────────────────────────────────────
                if has_spend:
                    ui.label("LiteLLM Spend Tracking").classes(
                        "text-sm font-semibold text-slate-600 mb-1"
                    )
                    with ui.row().classes("w-full gap-4 flex-wrap mb-4"):
                        total_cost = spend.get("total_cost", 0.0)
                        with ui.card().classes("flex-1 min-w-36 p-4 shadow-sm"):
                            with ui.row().classes("items-center gap-2 mb-1"):
                                ui.icon("attach_money", size="sm").classes("text-green-500")
                                ui.label("Total Spend").classes("text-xs text-slate-500 font-medium")
                            ui.label(f"${total_cost:.4f}").classes(
                                "text-2xl font-bold text-slate-800"
                            )

                        for model_entry in (spend.get("spend_by_model") or [])[:5]:
                            m_name = model_entry.get("model", "unknown")
                            m_cost = model_entry.get("total_cost", 0.0)
                            with ui.card().classes("flex-1 min-w-36 p-4 shadow-sm"):
                                ui.label(m_name[:24]).classes(
                                    "text-xs font-mono text-slate-500 mb-1 truncate"
                                )
                                ui.label(f"${m_cost:.4f}").classes(
                                    "text-xl font-bold text-slate-700"
                                )

                    if logs:
                        ui.label("Recent requests").classes(
                            "text-sm font-semibold text-slate-600 mb-2"
                        )
                        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
                            with ui.grid(columns=5).classes(
                                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
                            ):
                                for col in ["TIME", "MODEL", "TOKENS IN", "TOKENS OUT", "COST"]:
                                    ui.label(col).classes(
                                        "font-semibold text-slate-500 text-xs tracking-wider"
                                    )
                            with ui.column().classes("w-full"):
                                for entry in logs[:50]:
                                    ts = entry.get("startTime") or entry.get("created_at", "")
                                    model = entry.get("model", "")
                                    tok_in  = entry.get("prompt_tokens", "—")
                                    tok_out = entry.get("completion_tokens", "—")
                                    cost    = entry.get("spend", 0.0)
                                    with ui.grid(columns=5).classes(
                                        "w-full px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                                    ):
                                        ui.label(str(ts)[:19]).classes(
                                            "font-mono text-xs text-slate-500"
                                        )
                                        ui.label(model[:30]).classes(
                                            "text-xs text-slate-600 truncate"
                                        )
                                        ui.label(str(tok_in)).classes(
                                            "text-xs text-slate-600 font-mono"
                                        )
                                        ui.label(str(tok_out)).classes(
                                            "text-xs text-slate-600 font-mono"
                                        )
                                        ui.label(f"${cost:.5f}").classes(
                                            "text-xs text-slate-600 font-mono"
                                        )
                else:
                    with ui.row().classes("items-start gap-2 mb-3 bg-slate-50 rounded p-3"):
                        ui.icon("info", size="sm").classes("text-slate-400 shrink-0 mt-0.5")
                        ui.label(
                            "Token-level cost data is not available in this deployment. "
                            "Showing request counts from the audit log."
                        ).classes("text-xs text-slate-500")

                # ── Audit-based stats ──────────────────────────────────────────
                ui.label("Request stats (from audit log)").classes(
                    "text-sm font-semibold text-slate-600 mb-2"
                )
                by_model = audit_stats.get("by_model", {})
                total    = audit_stats.get("total", 0)

                if not total:
                    ui.label("No request data yet.").classes(
                        "text-slate-400 text-sm italic"
                    )
                    return

                with ui.row().classes("w-full gap-4"):
                    with ui.card().classes("flex-1 min-w-36 p-4 shadow-sm"):
                        with ui.row().classes("items-center gap-2 mb-1"):
                            ui.icon("chat", size="sm").classes("text-blue-500")
                            ui.label("Total Requests").classes("text-xs text-slate-500 font-medium")
                        ui.label(str(total)).classes("text-2xl font-bold text-slate-800")

                if by_model:
                    with ui.card().classes("w-full p-4 shadow-sm"):
                        ui.label("Requests per model").classes(
                            "text-xs font-semibold text-slate-500 mb-3"
                        )
                        max_count = max(by_model.values())
                        with ui.column().classes("w-full gap-2"):
                            for model_name, count in list(by_model.items())[:15]:
                                pct = int(count * 100 / max_count) if max_count else 0
                                with ui.column().classes("w-full gap-0.5"):
                                    with ui.row().classes("w-full justify-between"):
                                        ui.label(model_name[:40]).classes(
                                            "text-xs font-mono text-slate-600 truncate"
                                        )
                                        ui.label(str(count)).classes(
                                            "text-xs text-slate-500 font-mono shrink-0"
                                        )
                                    with ui.element("div").classes("w-full bg-slate-200 rounded h-1.5"):
                                        ui.element("div").classes(
                                            "bg-blue-500 h-1.5 rounded"
                                        ).style(f"width:{pct}%")

        refresh_btn.on("click", load_usage)
        asyncio.ensure_future(load_usage())


# ══════════════════════════════════════════════════════════════════════════════
# LlmModelsPermissions
# ══════════════════════════════════════════════════════════════════════════════

_LLM_PERM_ROLES = ["allowed_user"]


class LlmModelsPermissions:
    def __init__(self, fga: OpenFGA, llm_models: LLMModels) -> None:
        self.fga = fga
        self.llm_models = llm_models

    async def render(self, principals: dict[str, str]) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        label_to_key   = {v: k for k, v in principals.items()}
        subject_labels = list(principals.values())

        registered = await self.llm_models.list_registered()
        crud: list[CRUDTemplate] = []

        async def refresh_grants():
            reg = await self.llm_models.list_registered()
            if not reg:
                return []
            results = await asyncio.gather(
                *[self.fga.get_object_tuples(f"llm_model:{m}") for m in reg]
            )
            rows = []
            for mid, tuples in zip(reg, results):
                for subj, rel in tuples.items():
                    if rel not in _LLM_PERM_ROLES:
                        continue
                    rows.append({
                        "subject":      principals.get(subj, subj),
                        "role":         rel,
                        "model":        mid,
                        "_subject_key": subj,
                        "_obj":         f"llm_model:{mid}",
                    })
            return rows

        def open_grant_modal(item: dict | None = None) -> None:
            is_edit       = item is not None
            initial_subj  = item["subject"] if is_edit else (subject_labels[0] if subject_labels else None)
            initial_role  = item["role"]    if is_edit else _LLM_PERM_ROLES[0]
            initial_model = item["model"]   if is_edit else (registered[0] if registered else None)

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[480px] p-6 gap-4"):
                    ui.label("Edit access" if is_edit else "Grant access").classes("text-xl font-bold text-slate-800")
                    with ui.column().classes("w-full gap-3"):
                        subj_sel = ui.select(
                            subject_labels, value=initial_subj, label="User / Group"
                        ).classes("w-full").props("outlined dense")
                        role_sel = ui.select(
                            _LLM_PERM_ROLES, value=initial_role, label="Role"
                        ).classes("w-full").props("outlined dense")
                        model_sel = ui.select(
                            registered if registered else [], value=initial_model, label="Model"
                        ).classes("w-full").props("outlined dense use-input new-value-mode=add")
                        if not registered:
                            ui.label("No registered models — register them above first.").classes("text-xs text-amber-600")

                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def save(d=dlg):
                            subj  = label_to_key.get(subj_sel.value, subj_sel.value)
                            role  = role_sel.value
                            model = (model_sel.value or "").strip()
                            if not subj or not role or not model:
                                ui.notify("All fields are required.", type="warning")
                                return
                            obj = f"llm_model:{model}"
                            if is_edit:
                                old_subj = item["_subject_key"]
                                old_role = item["role"]
                                old_obj  = item["_obj"]
                                if subj == old_subj and role == old_role and obj == old_obj:
                                    d.close()
                                    return
                                await self.fga.write(
                                    writes=[{"user": subj, "relation": role, "object": obj}],
                                    deletes=[{"user": old_subj, "relation": old_role, "object": old_obj}],
                                )
                            else:
                                await self.fga.set_relation(subj, role, None, obj)
                            ui.notify("Access saved.", type="positive")
                            d.close()
                            if crud:
                                crud[0].refresh()

                        ui.button("Save", on_click=save).props("unelevated color=primary")

            dlg.open()

        async def on_delete_grant(item: dict):
            await self.fga.remove_relation(item["_subject_key"], item["role"], item["_obj"])
            ui.notify("Access removed.", type="info")

        tpl = CRUDTemplate(
            title="Model Access Grants",
            columns=["Subject", "Role", "Model"],
            on_refresh=refresh_grants,
            on_new_click=lambda: open_grant_modal(),
            on_edit=lambda item: open_grant_modal(item),
            on_delete=on_delete_grant,
            direct_edit=True,
        )
        crud.append(tpl)
