"""Chat page — multi-conversation UI with streaming responses and MCP tool calling."""

import asyncio
import re
import time
import uuid

from nicegui import app, ui

from config import DEFAULT_MODEL
from connectors.litellm import LiteLLM
from connectors.ollama import Ollama
from connectors.openfga import SHOKAN_OBJECT, OpenFGA
from services.chat import ChatService
from services.chat_storage import ChatStorage
from services.models import Models
from services.permissions import can

_ACTIVE_CHAT_KEY = "active_chat_id"
_LEGACY_CHATS_KEY = "chats"       # old app.storage.user key — migrated on first load
_LEGACY_HISTORY_KEY = "chat_history"
_MAX_HISTORY = 100                 # messages kept per chat (trimmed on save)
_MAX_CHATS   = 50                  # oldest chat deleted when exceeded


def _model_label(full_name: str) -> str:
    name = full_name
    if "/" in name and not name.startswith("hf.co/"):
        _, rest = name.split("/", 1)
        name = rest
    if name.startswith("hf.co/") and ":" in name:
        tag = name.split(":")[-1]
        return re.sub(r"\.gguf$", "", tag, flags=re.IGNORECASE)
    return name


def _model_options(models: list[str]) -> dict[str, str]:
    seen: dict[str, int] = {}
    result: dict[str, str] = {}
    for m in models:
        label = _model_label(m)
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 0
        result[m] = label
    return result


def _auto_title(text: str) -> str:
    text = text.strip()
    return (text[:40] + "…") if len(text) > 40 else text


class ChatPage:
    def __init__(self) -> None:
        self.models = Models()
        self.litellm = LiteLLM()
        self.ollama = Ollama()
        self.fga = OpenFGA()
        self.chat_service = ChatService()
        self.storage = ChatStorage()

    async def _filter_models(self, models: list[str], user_id: str, is_admin: bool) -> list[str]:
        if not models or is_admin:
            return models
        checks = await asyncio.gather(
            *[self.fga.check(user_id, "can_call", f"llm_model:{m}") for m in models]
        )
        return [m for m, ok in zip(models, checks) if ok]

    def _migrate_legacy(self, user_id: str) -> None:
        """One-time migration from app.storage.user to filesystem."""
        old_chats = dict(app.storage.user.get(_LEGACY_CHATS_KEY) or {})
        if old_chats:
            self.storage.migrate_from_storage(user_id, old_chats)
            app.storage.user.pop(_LEGACY_CHATS_KEY, None)
            return
        legacy = app.storage.user.get(_LEGACY_HISTORY_KEY)
        if legacy:
            cid = str(uuid.uuid4())
            self.storage.save_chat(user_id, cid, "Previous conversation", legacy, time.time())
            app.storage.user.pop(_LEGACY_HISTORY_KEY, None)

    async def render(self, user: dict, perms: set[str]) -> None:
        user_id  = user.get("id", "")
        username = user.get("username", "You")
        is_admin = await self.fga.check(user_id, "can_manage_services", SHOKAN_OBJECT)

        # ── Models ────────────────────────────────────────────────────────────
        available_now, installed = await asyncio.gather(
            self._filter_models(await self.models.available(), user_id, is_admin),
            self.models.installed_chat_models(),
        )
        all_models: list[str]   = list(dict.fromkeys(installed + available_now))
        available_set: set[str] = set(available_now)

        sel_val = app.storage.user.get("selected_model") or DEFAULT_MODEL
        _initial = sel_val if sel_val in all_models else (all_models[0] if all_models else "")
        selected_model: dict[str, str] = {"value": _initial}

        def _is_ready(m: str) -> bool:
            return m in available_set or (bool(m) and not m.startswith("ollama/"))

        # ── Chat storage bootstrap ─────────────────────────────────────────────
        self._migrate_legacy(user_id)
        storage = self.storage

        chat_list = storage.list_chats(user_id)
        active_id = app.storage.user.get(_ACTIVE_CHAT_KEY, "")
        known_ids  = {c["id"] for c in chat_list}

        if not chat_list or active_id not in known_ids:
            active_id = str(uuid.uuid4())
            storage.save_chat(user_id, active_id, "New chat", [], time.time())

        app.storage.user[_ACTIVE_CHAT_KEY] = active_id
        active_chat = storage.load_chat(user_id, active_id) or {
            "title": "New chat", "messages": [], "ts": time.time(),
            "compact_summary": "", "archived_raw": "",
        }
        state: dict = {
            "active_id": active_id,
            "history": list(active_chat["messages"]),
            "compact_summary": active_chat.get("compact_summary", ""),
        }

        # ── Page layout ───────────────────────────────────────────────────────
        with ui.row().classes("w-full gap-0 overflow-hidden").style("height: calc(100vh - 70px)"):

            # ── Sidebar ───────────────────────────────────────────────────────
            _btns: dict = {}
            search_state: dict = {"term": ""}

            with ui.column().classes(
                "w-52 shrink-0 border-r border-gray-200 h-full overflow-hidden flex flex-col bg-gray-50"
            ):
                _btns["new_chat"] = ui.button(
                    "New chat", icon="add",
                    on_click=lambda: _new_chat(),
                ).props("flat align=left").classes("w-full text-sm font-medium shrink-0 mt-1 px-2")
                if not state["history"]:
                    _btns["new_chat"].disable()

                search_input = (
                    ui.input(placeholder="Search chats…")
                    .props("outlined dense clearable")
                    .classes("mx-1 mt-1 shrink-0")
                )

                def _on_search(e) -> None:
                    search_state["term"] = (e.args or "").strip().lower()
                    _render_sidebar.refresh()

                search_input.on("update:modelValue", _on_search)

                ui.separator().classes("shrink-0")

                can_delete_chats = can(perms, "chat:history:delete") or is_admin

                with ui.column().classes("flex-1 overflow-y-auto gap-0 p-1"):
                    @ui.refreshable
                    def _render_sidebar() -> None:
                        chats = storage.list_chats(user_id)
                        term  = search_state["term"]
                        if term:
                            chats = [c for c in chats if term in c["title"].lower()]
                        active = state["active_id"]
                        for chat in chats:
                            cid = chat["id"]
                            is_active = cid == active
                            row_cls = (
                                "w-full items-center rounded px-2 py-1 cursor-pointer group "
                                + ("bg-blue-50" if is_active else "hover:bg-gray-100")
                            )
                            with ui.row().classes(row_cls).on(
                                "click", lambda cid=cid: _switch_chat(cid)
                            ):
                                ui.label(chat["title"]).classes(
                                    "flex-1 text-sm truncate "
                                    + ("text-blue-700 font-medium" if is_active else "text-gray-700")
                                )
                                if can_delete_chats:
                                    async def _delete_chat(cid: str = cid) -> None:
                                        chats_after = [c for c in storage.list_chats(user_id) if c["id"] != cid]
                                        storage.delete_chat(user_id, cid)
                                        if cid == state["active_id"]:
                                            if chats_after:
                                                next_chat = storage.load_chat(user_id, chats_after[0]["id"])
                                                state["active_id"]       = chats_after[0]["id"]
                                                state["history"]         = list(next_chat["messages"]) if next_chat else []
                                                state["compact_summary"] = next_chat.get("compact_summary", "") if next_chat else ""
                                            else:
                                                new_id = str(uuid.uuid4())
                                                storage.save_chat(user_id, new_id, "New chat", [], time.time())
                                                state["active_id"]       = new_id
                                                state["history"]         = []
                                                state["compact_summary"] = ""
                                            app.storage.user[_ACTIVE_CHAT_KEY] = state["active_id"]
                                            _replay_messages()
                                            _sync_new_chat_btn()
                                        _render_sidebar.refresh()

                                    (
                                        ui.button(icon="close")
                                        .props("flat round dense size=xs")
                                        .classes("text-gray-400 hover:text-red-500 shrink-0 opacity-0 group-hover:opacity-100")
                                        .on("click.stop", _delete_chat)
                                    )

                    _render_sidebar()

            # ── Main chat area ────────────────────────────────────────────────
            with ui.column().classes("flex-1 p-4 flex flex-col gap-2 overflow-hidden h-full"):

                messages_box = ui.column().classes("flex-1 w-full gap-2 overflow-y-auto pb-2")

                async def _scroll_bottom() -> None:
                    await ui.run_javascript(
                        f'var el=document.getElementById("c{messages_box.id}");'
                        f'if(el)el.scrollTop=el.scrollHeight;'
                    )

                def _replay_messages() -> None:
                    messages_box.clear()
                    with messages_box:
                        for msg in state["history"]:
                            if msg["role"] == "user":
                                ui.chat_message(text=msg["content"], name=username, sent=True)
                            elif msg["role"] == "assistant":
                                ui.markdown(msg["content"]).classes(
                                    "bg-gray-100 rounded-lg p-3 text-sm w-full"
                                )

                _replay_messages()
                ui.timer(0.1, _scroll_bottom, once=True)

                # ── Chat management helpers ────────────────────────────────────

                def _save_current() -> None:
                    cid = state["active_id"]
                    existing = storage.load_chat(user_id, cid)
                    title = existing["title"] if existing else "New chat"
                    storage.save_chat(user_id, cid, title, state["history"][-_MAX_HISTORY:], time.time())

                def _sync_new_chat_btn() -> None:
                    if state["history"]:
                        _btns["new_chat"].enable()
                    else:
                        _btns["new_chat"].disable()

                async def _new_chat() -> None:
                    _save_current()
                    new_id = str(uuid.uuid4())
                    chats = storage.list_chats(user_id)
                    if len(chats) >= _MAX_CHATS:
                        storage.delete_chat(user_id, chats[-1]["id"])  # oldest is last
                    storage.save_chat(user_id, new_id, "New chat", [], time.time())
                    app.storage.user[_ACTIVE_CHAT_KEY] = new_id
                    state["active_id"]       = new_id
                    state["history"]         = []
                    state["compact_summary"] = ""
                    _replay_messages()
                    _sync_new_chat_btn()
                    _render_sidebar.refresh()

                async def _switch_chat(cid: str) -> None:
                    if cid == state["active_id"]:
                        return
                    _save_current()
                    chat = storage.load_chat(user_id, cid)
                    if not chat:
                        return
                    state["active_id"]       = cid
                    state["history"]         = list(chat["messages"])
                    state["compact_summary"] = chat.get("compact_summary", "")
                    app.storage.user[_ACTIVE_CHAT_KEY] = cid
                    _replay_messages()
                    _sync_new_chat_btn()
                    await _scroll_bottom()
                    _render_sidebar.refresh()

                # ── Send message ───────────────────────────────────────────────
                _no_model_placeholder = "Select a model below to start chatting"
                _initial_ready = _is_ready(_initial)

                async def send_message() -> None:
                    text = msg_input.value.strip()
                    if not text:
                        return
                    _sel = selected_model["value"]
                    if not _sel or _sel == "—":
                        ui.notify("Select a model to start chatting.", type="warning")
                        return

                    msg_input.value = ""
                    send_btn.disable()
                    msg_input.disable()

                    with messages_box:
                        ui.chat_message(text=text, name=username, sent=True)
                    await _scroll_bottom()

                    first_message = len(state["history"]) == 0
                    state["history"].append({"role": "user", "content": text})

                    if first_message:
                        cid = state["active_id"]
                        existing = storage.load_chat(user_id, cid)
                        ts = existing["ts"] if existing else time.time()
                        storage.save_chat(user_id, cid, _auto_title(text), state["history"], ts)
                        _sync_new_chat_btn()
                        _render_sidebar.refresh()

                    # ── Streaming response ─────────────────────────────────────
                    with messages_box:
                        reply_label = ui.markdown("▋").classes(
                            "bg-gray-100 rounded-lg p-3 text-sm w-full"
                        )
                    await _scroll_bottom()

                    reply_parts: list[str] = []
                    _token_count = 0

                    async def _loading_guard() -> None:
                        await asyncio.sleep(5)
                        if not reply_parts:
                            ui.notify(
                                "Model is loading into RAM, please wait (up to 2 min)…",
                                type="info",
                                timeout=120000,
                            )

                    guard_task = asyncio.create_task(_loading_guard())
                    try:
                        temperature = float(app.storage.user.get("temperature", 0.0))

                        # Prepend compact summary so the LLM has full conversation context
                        llm_history = list(state["history"])
                        if state.get("compact_summary"):
                            llm_history = [
                                {
                                    "role": "system",
                                    "content": f"Summary of earlier conversation:\n{state['compact_summary']}",
                                },
                                *llm_history,
                            ]

                        stream = await self.chat_service.stream_response(
                            user_id=user_id,
                            model=_sel,
                            history=llm_history,
                            litellm_url=self.litellm.url,
                            litellm_headers=self.litellm._headers,
                            ollama_url=self.ollama.url,
                            temperature=temperature,
                        )
                        async for token in stream:
                            guard_task.cancel()
                            reply_parts.append(token)
                            reply_label.content = "".join(reply_parts) + "▋"
                            _token_count += 1
                            if _token_count % 8 == 0:
                                await _scroll_bottom()
                            await asyncio.sleep(0)
                    except Exception as exc:
                        reply_parts.append(f"Error: {exc}")
                    finally:
                        guard_task.cancel()
                        if _is_ready(selected_model["value"]):
                            send_btn.enable()
                            msg_input.enable()

                    reply = "".join(reply_parts)
                    reply_label.content = reply
                    await _scroll_bottom()
                    state["history"].append({"role": "assistant", "content": reply})
                    _save_current()

                    # Compact if enough messages have accumulated in the file
                    if storage.needs_compaction(user_id, state["active_id"]):
                        compact_data = storage.get_compaction_data(user_id, state["active_id"])
                        if compact_data:
                            to_compact, existing_summary = compact_data
                            new_summary = await self.chat_service.compact_messages(
                                to_compact,
                                existing_summary,
                                ollama_url=self.ollama.url,
                                fallback_url=self.litellm.url,
                                fallback_model=_sel,
                                fallback_headers=self.litellm._headers,
                            )
                            if new_summary:
                                remaining = storage.apply_compaction(
                                    user_id, state["active_id"], new_summary
                                )
                                # Sync in-memory state (messages disappear from LLM context,
                                # stay visible in UI until page reload or chat switch)
                                state["history"]         = remaining
                                state["compact_summary"] = new_summary

                # ── Input row ──────────────────────────────────────────────────
                with ui.row().classes("w-full gap-2 items-end shrink-0"):
                    msg_input = (
                        ui.input(
                            placeholder="Ask Shokan..." if _initial_ready else _no_model_placeholder
                        )
                        .classes("flex-1")
                        .props("outlined dense")
                        .mark("msg-input")
                    )
                    if not _initial_ready:
                        msg_input.disable()
                    send_btn = (
                        ui.button(icon="send", on_click=send_message)
                        .props("flat round")
                        .mark("send-btn")
                    )
                    if not _initial_ready:
                        send_btn.disable()

                msg_input.on("keydown.enter", send_message)

                # ── Bottom bar ─────────────────────────────────────────────────
                async def _refresh_models() -> None:
                    now, inst = await asyncio.gather(
                        self._filter_models(await self.models.available(), user_id, is_admin),
                        self.models.installed_chat_models(),
                    )
                    merged = list(dict.fromkeys(inst + now))
                    available_set.clear()
                    available_set.update(now)
                    all_models.clear()
                    all_models.extend(merged)
                    if merged:
                        model_select.options = _model_options(merged)
                        model_select.set_enabled(can_change_model)
                        if selected_model["value"] not in merged:
                            selected_model["value"] = merged[0]
                            model_select.value = merged[0]
                        cur   = selected_model["value"]
                        ready = _is_ready(cur)
                        msg_input.placeholder = "Ask Shokan..." if ready else _no_model_placeholder
                        if ready:
                            msg_input.enable()
                            send_btn.enable()
                            load_btn.set_visibility(False)
                        else:
                            msg_input.disable()
                            send_btn.disable()
                            load_btn.set_visibility(can_load_model and bool(cur) and cur.startswith("ollama/"))
                            load_btn.enable()
                    else:
                        model_select.set_enabled(False)
                        msg_input.disable()
                        msg_input.placeholder = _no_model_placeholder
                        send_btn.disable()
                        load_btn.set_visibility(False)
                    ui.notify("Model list refreshed.", type="info", timeout=2000)

                async def _load_model(model: str) -> None:
                    if not model or model == "—" or not model.startswith("ollama/"):
                        return
                    label = _model_label(model)
                    raw   = model[len("ollama/"):]
                    msg_input.disable()
                    msg_input.placeholder = f"Loading {label}…"
                    send_btn.disable()
                    load_btn.disable()
                    ui.notify(f"Loading {label}…", type="info", timeout=30000)
                    try:
                        await self.ollama.load(raw)
                        available_set.add(model)
                        msg_input.enable()
                        msg_input.placeholder = "Ask Shokan..."
                        send_btn.enable()
                        load_btn.set_visibility(False)
                        ui.notify(f"{label} ready.", type="positive", timeout=3000)
                    except Exception as exc:
                        load_btn.enable()
                        ui.notify(f"Failed to load {label}: {exc}", type="negative")

                async def _on_model_change(e) -> None:
                    new_model = e.args
                    if not new_model or new_model == "—":
                        return
                    selected_model["value"] = new_model
                    app.storage.user["selected_model"] = new_model
                    if _is_ready(new_model):
                        msg_input.enable()
                        msg_input.placeholder = "Ask Shokan..."
                        send_btn.enable()
                        load_btn.set_visibility(False)
                    else:
                        msg_input.disable()
                        msg_input.placeholder = _no_model_placeholder
                        send_btn.disable()
                        if can_load_model and new_model.startswith("ollama/"):
                            load_btn.enable()
                            load_btn.set_visibility(True)
                        else:
                            load_btn.set_visibility(False)

                can_change_model = can(perms, "chat:model:update")
                can_load_model   = can(perms, "models:ollama:start")

                with ui.row().classes("w-full justify-end items-center gap-1 mt-1 shrink-0"):
                    with ui.row().classes("items-center gap-1"):
                        ui.button(icon="refresh", on_click=_refresh_models).props(
                            "flat round dense"
                        ).tooltip("Refresh model list")

                        model_select = (
                            ui.select(
                                _model_options(all_models) if all_models else {"—": "—"},
                                value=_initial or "—",
                                label="Model",
                            )
                            .classes("w-52")
                            .props("dense outlined")
                            .mark("model-select")
                        )
                        if not can_change_model:
                            model_select.disable()
                        model_select.on("update:modelValue", _on_model_change)

                        _show_load_btn = (
                            can_load_model and bool(_initial) and not _initial_ready
                            and _initial.startswith("ollama/")
                        )

                        async def _do_load() -> None:
                            await _load_model(selected_model["value"])

                        load_btn = (
                            ui.button(icon="play_arrow", on_click=_do_load)
                            .props("flat round dense color=positive")
                            .tooltip("Load model into RAM")
                            .mark("load-btn")
                        )
                        load_btn.set_visibility(_show_load_btn)
