"""
Shokan-LLM profile settings — page class.

Imported by main.py and rendered at /profile.
No entrypoint, no auth routes, no standalone startup.
Requires a NiceGUI @ui.page context to call render().
"""

import time

from nicegui import app as nicegui_app
from nicegui import ui

from config import DEFAULT_MODEL
from connectors.mcp import SERVERS as MCP_SERVERS
from connectors.openfga import SHOKAN_OBJECT, OpenFGA
from services.chat_storage import ChatStorage
from services.models import Models
from services.permissions import CATALOG, SECTION_LABELS

# Must match key in pages/chat.py
_ACTIVE_CHAT_KEY = "active_chat_id"

def _fmt_ts(ts: float) -> str:
    """Human-readable relative date for a Unix timestamp."""
    if not ts:
        return ""
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


_ROLE_COLORS = {
    "admin": "red",
    "member": "blue",
    "none": "grey",
}


class ProfileView:
    """Renders the user profile and access overview page.

    Instantiate once per application startup; call render(user, perms) per page visit.
    """

    def __init__(self) -> None:
        self.fga = OpenFGA()
        self.models = Models()
        self.storage = ChatStorage()

    async def render(self, user: dict, perms: set[str]) -> None:
        """Build the profile UI. Must be called within a NiceGUI page context."""
        user_id = user.get("id", "")
        with ui.column().classes("w-full max-w-2xl gap-4"):
            await self._render_identity_card(user)
            self._render_permissions_card(perms)
            await self._render_preferences_card()
            self._render_chats_card(user_id)

    # ── Cards ──────────────────────────────────────────────────────────────────

    async def _render_identity_card(self, user: dict) -> None:
        """Single compact card: identity + role + MCP server access."""
        try:
            tuples = await self.fga.get_object_tuples(SHOKAN_OBJECT)
            role = tuples.get(f"user:{user.get('id', '')}", "none")
        except Exception:
            role = "none"
        color = _ROLE_COLORS.get(role, "grey")

        with ui.card().classes("w-full"):
            # Identity + role on one row
            with ui.row().classes("w-full items-center gap-3"):
                ui.icon("account_circle", size="lg").classes("text-gray-400 shrink-0")
                with ui.column().classes("flex-1 gap-0 min-w-0"):
                    display = user.get("name") or user.get("username", "")
                    ui.label(display).classes("text-base font-semibold truncate")
                    ui.label(user.get("email", "")).classes("text-sm text-gray-400 truncate")
                    ui.label(f"ID: {user.get('id', '')}").classes("text-xs text-gray-300 font-mono")
                ui.badge(role, color=color).classes("shrink-0 self-start mt-1")

            ui.separator().classes("my-2")

            # MCP access on one row
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                ui.label("MCP Access:").classes("text-xs text-gray-400 shrink-0")
                try:
                    subj = user.get("id", "")
                    for sid in MCP_SERVERS:
                        has_access = await self.fga.check(subj, "can_use", f"mcp_server:{sid}")
                        ui.badge(sid, color="green" if has_access else "grey")
                except Exception as exc:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-xs")
            ui.label("Verde = acceso concedido vía tu rol o grupo").classes("text-xs text-gray-300 mt-1")

    def _render_permissions_card(self, perms: set[str]) -> None:
        """Show the user's effective UI permissions grouped by section."""
        with ui.card().classes("w-full"):
            ui.label("Permisos de interfaz").classes("font-semibold mb-1")
            ui.label(
                "Permisos efectivos que se te aplican (rol + usuario + grupos combinados)."
            ).classes("text-xs text-gray-400 mb-3")

            if not perms:
                ui.label("Sin permisos asignados.").classes("text-sm text-gray-400")
                return

            # Group catalog entries by section
            by_section: dict[str, list[dict]] = {}
            for entry in CATALOG:
                by_section.setdefault(entry["section"], []).append(entry)

            for section, entries in by_section.items():
                granted = [e for e in entries if e["id"] in perms]
                if not granted:
                    continue
                with ui.column().classes("gap-1 mb-3"):
                    ui.label(SECTION_LABELS.get(section, section)).classes(
                        "text-xs font-semibold text-gray-500 uppercase"
                    )
                    with ui.row().classes("flex-wrap gap-1"):
                        for entry in granted:
                            label = f"{entry['label']} · {entry['action']}"
                            ui.badge(label, color="blue").classes("text-xs")

    async def _render_preferences_card(self) -> None:
        system_default = DEFAULT_MODEL
        selected_model = nicegui_app.storage.user.get("selected_model") or system_default
        temperature = float(nicegui_app.storage.user.get("temperature", 0.0))

        available_models = await self.models.installed_chat_models()

        with ui.card().classes("w-full"):
            ui.label("Preferences").classes("font-semibold mb-3")

            # ── Model ──────────────────────────────────────────────────────────
            ui.label("Selected model").classes("text-sm text-gray-500 mb-1")
            _ref: dict = {}
            if available_models:
                if selected_model not in available_models:
                    selected_model = available_models[0]
                _ref["model"] = ui.select(
                    available_models,
                    value=selected_model,
                    label="Model",
                ).classes("w-full").props("dense outlined")
            else:
                _ref["model"] = ui.input("Model ID", value=selected_model).classes("w-full")

            # ── Temperature ────────────────────────────────────────────────────
            ui.separator().classes("my-4")
            with ui.row().classes("items-center gap-1 mb-1"):
                ui.label("Temperature").classes("text-sm text-gray-500")
                ui.icon("info_outline", size="xs").classes("text-gray-400").tooltip(
                    "Controla la aleatoriedad de las respuestas.\n"
                    "0 = determinista y preciso (ideal para código, hechos).\n"
                    "1 = creativo y variado (ideal para brainstorming, redacción).\n"
                    "Se aplica a todos los modelos (Ollama y LiteLLM)."
                )
            temp_input = ui.number(
                value=temperature,
                min=0.0,
                max=1.0,
                step=0.1,
                format="%.1f",
                label="Temperature",
            ).classes("w-full").props("dense outlined")
            ui.label(
                "0 = determinista y preciso · 1 = creativo y variado"
            ).classes("text-xs text-gray-400 mt-1")

            # ── Save ───────────────────────────────────────────────────────────
            def save_prefs() -> None:
                nicegui_app.storage.user["selected_model"] = _ref["model"].value
                temp = round(max(0.0, min(1.0, float(temp_input.value or 0.0))), 2)
                nicegui_app.storage.user["temperature"] = temp
                ui.notify("Preferences saved", type="positive")

            ui.button("Save", on_click=save_prefs, icon="save").props("flat dense").classes("mt-3")

    def _render_chats_card(self, user_id: str) -> None:
        storage = self.storage
        with ui.card().classes("w-full"):
            ui.label("Conversations").classes("font-semibold mb-1")
            ui.label("Manage your saved conversations.").classes("text-xs text-gray-400 mb-3")

            @ui.refreshable
            def _chat_list() -> None:
                chats    = storage.list_chats(user_id)
                active_id: str = nicegui_app.storage.user.get(_ACTIVE_CHAT_KEY, "")

                if not chats:
                    ui.label("No conversations yet.").classes("text-sm text-gray-400")
                    return

                for chat in chats:
                    cid       = chat["id"]
                    n_msgs    = chat["message_count"]
                    date_str  = _fmt_ts(chat["ts"])
                    is_active = cid == active_id

                    with ui.row().classes("w-full items-center py-2 border-b border-gray-100 gap-2"):
                        with ui.column().classes("flex-1 gap-0 min-w-0"):
                            with ui.row().classes("items-center gap-2"):
                                ui.label(chat["title"]).classes("text-sm font-medium truncate")
                                if is_active:
                                    ui.badge("active", color="blue").classes("text-xs shrink-0")
                            ui.label(f"{n_msgs} messages · {date_str}").classes("text-xs text-gray-400")

                        ui.button(
                            icon="delete_sweep",
                            on_click=lambda cid=cid: _clear_chat(cid),
                        ).props("flat round dense color=grey").tooltip("Clear messages")

                        ui.button(
                            icon="delete",
                            on_click=lambda cid=cid: _delete_chat(cid),
                        ).props("flat round dense color=negative").tooltip("Delete conversation")

            def _clear_chat(cid: str) -> None:
                storage.clear_chat(user_id, cid)
                ui.notify("Messages cleared.", type="info", timeout=2000)
                _chat_list.refresh()

            def _delete_chat(cid: str) -> None:
                storage.delete_chat(user_id, cid)
                if nicegui_app.storage.user.get(_ACTIVE_CHAT_KEY) == cid:
                    nicegui_app.storage.user.pop(_ACTIVE_CHAT_KEY, None)
                ui.notify("Conversation deleted.", type="positive", timeout=2000)
                _chat_list.refresh()

            _chat_list()
