"""
Shokan-LLM system settings — page class.

Imported by main.py; rendered at /system-settings.
No entrypoint, no auth routes, no standalone startup.
Requires a NiceGUI @ui.page context to call render().
"""

import asyncio
import datetime
import os

from nicegui import ui
from qdrant_client import QdrantClient

import json

from connectors.k8s import K8s
from connectors.keycloak import Keycloak
from connectors.rag import get_top_k, scroll_qdrant_index, set_top_k
from services.audit import AuditLog
from services.chat_storage import ChatStorage
from services.export_import import ExportImportService, SECTIONS
from services.permissions import can

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant.shokanllm.svc.cluster.local:6333")
_COLLECTION = "shokan_rag"

_CRONJOB_META: dict[str, dict] = {
    "shokan-core-ingester": {
        "name": "RAG",
        "description": "Ingests enabled data sources (GDrive, S3, SFTP, Filesystem) into Qdrant via Ollama embeddings.",
    },
    "shokan-core-chat-cleanup": {
        "name": "Chat Cleanup",
        "description": "Deletes chat conversations whose last-updated timestamp exceeds the retention period.",
        "retention_env_var": "CHAT_RETENTION_DAYS",
        "retention_default": "30",
    },
}

_SOURCE_ICONS = {
    "gdrive":     ("cloud",     "Google Drive"),
    "s3":         ("inventory", "Amazon S3"),
    "filesystem": ("folder",    "Filesystem"),
    "sftp":       ("terminal",  "SFTP"),
}

_CJ_COLS     = 7  # Name | Description | Schedule | Status | Last OK | AGE | Actions
_DOC_COLS    = 4  # Source | File | Chunks | Datasource
_CHAT_COLS   = 4  # User | Chats | Size | Actions
_AUDIT_COLS  = 5  # Timestamp | User | Action | Resource | Details


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 ** 2:.1f} MB"


def _fmt_age(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        h, m = divmod(secs // 60, 60)
        return f"{h}h {m}m" if m else f"{h}h"
    d, rem = divmod(secs, 86400)
    return f"{d}d {rem // 3600}h" if rem // 3600 else f"{d}d"


def _display_path(source_type: str, file_path: str) -> str:
    """Return a short, human-friendly path for display."""
    if source_type == "gdrive":
        # format: gdrive:<file_id>:<name>
        parts = file_path.split(":", 2)
        return parts[2] if len(parts) == 3 else file_path
    if source_type == "s3":
        # format: s3://<bucket>/<key>
        without_scheme = file_path[5:] if file_path.startswith("s3://") else file_path
        slash = without_scheme.find("/")
        return without_scheme[slash + 1:] if slash != -1 else without_scheme
    return file_path


# ══════════════════════════════════════════════════════════════════════════════
# SystemView
# ══════════════════════════════════════════════════════════════════════════════


class SystemView:
    def __init__(self) -> None:
        self.k8s     = K8s()
        self.qdrant  = QdrantClient(url=QDRANT_URL)
        self.kc      = Keycloak()
        self.storage = ChatStorage()
        self.audit   = AuditLog()
        self.export_svc = ExportImportService()

    async def render(self, user: dict, perms: set[str]) -> None:
        ui.label("System").classes("text-2xl font-bold mb-1")
        ui.label("Platform infrastructure and scheduled tasks.").classes("text-sm text-gray-500 mb-4")

        with ui.tabs().classes("w-full bg-gray-100") as tabs:
            tab_cj         = ui.tab("CronJobs",          icon="schedule")
            tab_rag        = ui.tab("Indexed Documents",  icon="description")
            tab_rag_params = ui.tab("RAG Settings",       icon="tune")
            tab_chats      = ui.tab("Chat Management",    icon="forum")
            tab_dashboard  = ui.tab("Dashboard",          icon="bar_chart")    if can(perms, "system:dashboard:read") else None
            tab_audit      = ui.tab("Audit Log",          icon="policy")       if can(perms, "system:audit:read")     else None
            tab_export     = ui.tab("Export",             icon="download")     if can(perms, "system:export:read")    else None
            tab_import     = ui.tab("Import",             icon="upload")       if can(perms, "system:export:write")   else None

        with ui.tab_panels(tabs, value=tab_cj).classes("w-full"):
            with ui.tab_panel(tab_cj):
                if can(perms, "system:cronjobs:read"):
                    await self._render_cronjobs(perms)
                else:
                    ui.label("Access denied.").classes("text-gray-500 text-sm p-4")
            with ui.tab_panel(tab_rag):
                if can(perms, "system:rag_index:read"):
                    self._render_rag_index()
                else:
                    ui.label("Access denied.").classes("text-gray-500 text-sm p-4")
            with ui.tab_panel(tab_rag_params):
                if can(perms, "system:rag_params:read"):
                    await self._render_rag_params(perms)
                else:
                    ui.label("Access denied.").classes("text-gray-500 text-sm p-4")
            with ui.tab_panel(tab_chats):
                if can(perms, "system:chats:read"):
                    await self._render_chat_management(perms)
                else:
                    ui.label("Access denied.").classes("text-gray-500 text-sm p-4")
            if tab_dashboard:
                with ui.tab_panel(tab_dashboard):
                    await self._render_dashboard()
            if tab_audit:
                with ui.tab_panel(tab_audit):
                    self._render_audit_log()
            if tab_export:
                with ui.tab_panel(tab_export):
                    await self._render_export_tab()
            if tab_import:
                with ui.tab_panel(tab_import):
                    self._render_import_tab()

    # ── CronJobs ───────────────────────────────────────────────────────────────

    async def _render_cronjobs(self, perms: set[str]) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        can_update = can(perms, "system:cronjobs:update")

        with ui.row().classes("w-full justify-between items-center mb-2"):
            ui.label("CronJobs").classes("text-lg font-semibold text-slate-800")
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
            with ui.grid(columns=_CJ_COLS).classes(
                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
            ):
                for col in ["NAME", "DESCRIPTION", "SCHEDULE", "STATUS", "LAST OK", "AGE", ""]:
                    ui.label(col).classes("font-semibold text-slate-500 text-xs tracking-wider")

            rows_container = ui.column().classes("w-full")

        async def load_rows():
            rows_container.clear()
            try:
                jobs = await asyncio.to_thread(self.k8s.list_cronjobs)
            except Exception as exc:
                with rows_container:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-sm px-4 py-3")
                return

            known = [j for j in jobs if j["k8s_name"] in _CRONJOB_META]
            if not known:
                with rows_container:
                    ui.label("No CronJobs found.").classes("text-slate-400 text-sm italic px-4 py-3")
                return

            for job in known:
                meta      = _CRONJOB_META[job["k8s_name"]]
                suspended = job["suspended"]
                with rows_container:
                    with ui.grid(columns=_CJ_COLS).classes(
                        "w-full items-center px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                    ):
                        ui.label(meta["name"]).classes("font-medium text-slate-700 text-sm")
                        ui.label(meta["description"]).classes("text-slate-500 text-xs")
                        ui.label(job["schedule"]).classes("font-mono text-sm text-slate-700")
                        with ui.row().classes("items-center gap-1"):
                            ui.icon("circle", size="xs").classes(
                                "text-red-400" if suspended else "text-green-500"
                            )
                            ui.label("Stopped" if suspended else "Running").classes("text-sm text-slate-600")
                        ok = job.get("last_ok_secs")
                        ui.label(_fmt_age(ok) + " ago" if ok is not None else "Never").classes(
                            "text-sm " + ("text-slate-500" if ok is not None else "text-slate-300 italic")
                        )
                        ui.label(_fmt_age(job["age_secs"])).classes("text-sm text-slate-500")

                        with ui.row().classes("items-center justify-end gap-1"):
                            if can_update:
                                if suspended:
                                    ui.button(
                                        icon="play_arrow",
                                        on_click=lambda j=job: _toggle(j, False),
                                    ).props("flat round dense color=positive").tooltip("Start schedule").mark("cj-toggle-btn")
                                else:
                                    ui.button(
                                        icon="stop",
                                        on_click=lambda j=job: _toggle(j, True),
                                    ).props("flat round dense color=negative").tooltip("Stop schedule").mark("cj-toggle-btn")

                                ui.button(
                                    icon="play_circle",
                                    on_click=lambda j=job: _run_now(j),
                                ).props("flat round dense color=positive").tooltip("Run now").mark("cj-run-btn")

                                ui.button(
                                    icon="edit_calendar",
                                    on_click=lambda j=job: _open_schedule_modal(j),
                                ).props("flat round dense color=primary").tooltip("Edit schedule").mark("cj-edit-btn")

        async def _run_now(job: dict) -> None:
            try:
                await asyncio.to_thread(self.k8s.trigger_cronjob, job["k8s_name"])
                label = _CRONJOB_META[job["k8s_name"]]["name"]
                ui.notify(f"{label} triggered — check logs for progress.", type="positive")
            except Exception as exc:
                ui.notify(f"Error: {exc}", type="negative")

        async def _toggle(job: dict, suspend: bool) -> None:
            try:
                await asyncio.to_thread(self.k8s.suspend_cronjob, job["k8s_name"], suspend)
                label = _CRONJOB_META[job["k8s_name"]]["name"]
                ui.notify(f"{label} {'stopped' if suspend else 'started'}.", type="positive")
                await load_rows()
            except Exception as exc:
                ui.notify(f"Error: {exc}", type="negative")

        async def _open_schedule_modal(job: dict) -> None:
            meta = _CRONJOB_META[job["k8s_name"]]
            retention_var     = meta.get("retention_env_var")
            retention_default = meta.get("retention_default", "30")
            current_retention = retention_default
            if retention_var:
                try:
                    val = await asyncio.to_thread(
                        self.k8s.get_cronjob_env, job["k8s_name"], retention_var
                    )
                    if val and val.strip().lstrip("-").isdigit():
                        current_retention = val
                except Exception:
                    pass

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[440px] p-6 gap-4"):
                    ui.label(f"Edit — {meta['name']}").classes("text-xl font-bold text-slate-800")

                    ui.label("Schedule").classes("text-sm font-semibold text-slate-600 mt-1")
                    ui.label("minute  hour  day-of-month  month  day-of-week").classes(
                        "font-mono text-xs text-gray-400"
                    )
                    schedule_inp = (
                        ui.input(label="Cron expression", value=job["schedule"])
                        .classes("w-full font-mono")
                        .props("outlined dense")
                    )

                    retention_inp = None
                    if retention_var:
                        ui.separator().classes("my-2")
                        ui.label("Retention").classes("text-sm font-semibold text-slate-600")
                        ui.label(
                            "Conversations whose last update is older than this many days will be deleted."
                        ).classes("text-xs text-gray-400 mb-1")
                        retention_inp = (
                            ui.number(
                                label="Retention (days)",
                                value=int(current_retention),
                                min=1,
                                max=3650,
                                step=1,
                                format="%d",
                            )
                            .classes("w-full")
                            .props("outlined dense")
                        )

                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def _save(d=dlg):
                            val = schedule_inp.value.strip()
                            if not val:
                                ui.notify("Schedule cannot be empty.", type="warning")
                                return
                            try:
                                await asyncio.to_thread(
                                    self.k8s.patch_cronjob_schedule, job["k8s_name"], val
                                )
                                if retention_inp is not None and retention_var:
                                    ret_val = str(int(retention_inp.value or 30))
                                    await asyncio.to_thread(
                                        self.k8s.patch_cronjob_env,
                                        job["k8s_name"],
                                        retention_var,
                                        ret_val,
                                    )
                                ui.notify("Settings updated.", type="positive")
                                d.close()
                                await load_rows()
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")

                        ui.button("Save", on_click=_save).props("unelevated color=primary")
            dlg.open()

        refresh_btn.on("click", load_rows)
        asyncio.ensure_future(load_rows())

    # ── Indexed Documents ──────────────────────────────────────────────────────

    def _render_rag_index(self) -> None:
        ui.label("Indexed Documents").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label(
            "Files indexed from all active data sources. Each row is a unique file with its chunk count."
        ).classes("text-sm text-gray-500 mb-3")

        with ui.row().classes("w-full justify-end mb-2"):
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        summary_label = ui.label("").classes("text-xs text-slate-400 mb-2")

        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
            with ui.grid(columns=_DOC_COLS).classes(
                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
            ):
                for col in ["SOURCE", "FILE", "CHUNKS", "DATASOURCE"]:
                    ui.label(col).classes("font-semibold text-slate-500 text-xs tracking-wider")

            rows_box = ui.column().classes("w-full")

        async def load_docs():
            rows_box.clear()
            summary_label.set_text("")
            with rows_box:
                ui.spinner(size="sm").classes("mx-auto my-4")

            try:
                docs = await asyncio.to_thread(scroll_qdrant_index, self.qdrant)
            except Exception as exc:
                rows_box.clear()
                with rows_box:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-sm px-4 py-3")
                return

            rows_box.clear()
            if not docs:
                with rows_box:
                    ui.label("No documents indexed yet.").classes(
                        "text-slate-400 text-sm italic px-4 py-3"
                    )
                return

            total_chunks = sum(d["chunks"] for d in docs)
            summary_label.set_text(f"{len(docs)} files · {total_chunks} chunks")

            with rows_box:
                for doc in docs:
                    st   = doc["source_type"]
                    icon, label = _SOURCE_ICONS.get(st, ("help_outline", st))
                    path = _display_path(st, doc["file_path"])
                    with ui.grid(columns=_DOC_COLS).classes(
                        "w-full items-center px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                    ):
                        with ui.row().classes("items-center gap-1"):
                            ui.icon(icon, size="xs").classes("text-slate-400 shrink-0")
                            ui.label(label).classes("text-sm text-slate-600")
                        ui.label(path).classes(
                            "text-sm text-slate-700 font-mono truncate"
                        ).style("max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;")
                        ui.label(str(doc["chunks"])).classes("text-sm text-slate-500")
                        ui.label(doc["datasource_id"]).classes("text-xs text-slate-400 font-mono truncate")

        refresh_btn.on("click", load_docs)
        asyncio.ensure_future(load_docs())

    # ── RAG Settings ───────────────────────────────────────────────────────────

    async def _render_rag_params(self, perms: set[str]) -> None:
        can_update = can(perms, "system:rag_params:update")

        ui.label("RAG Settings").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label(
            "RAG_TOP_K takes effect immediately for the running app. "
            "CHUNK_SIZE and CHUNK_OVERLAP apply to the next ingester run."
        ).classes("text-sm text-gray-500 mb-4")

        # ── Load current values ────────────────────────────────────────────────
        top_k_val = get_top_k()
        chunk_size_val = int(os.getenv("CHUNK_SIZE", "1000"))
        chunk_overlap_val = int(os.getenv("CHUNK_OVERLAP", "200"))
        try:
            stored_top_k = await asyncio.to_thread(self.k8s.read, "rag-top-k")
            if stored_top_k:
                top_k_val = int(stored_top_k)
            raw_cs = await asyncio.to_thread(
                self.k8s.get_cronjob_env, "shokan-core-ingester", "CHUNK_SIZE"
            )
            raw_co = await asyncio.to_thread(
                self.k8s.get_cronjob_env, "shokan-core-ingester", "CHUNK_OVERLAP"
            )
            if raw_cs:
                chunk_size_val = int(raw_cs)
            if raw_co:
                chunk_overlap_val = int(raw_co)
        except Exception:
            pass

        with ui.card().classes("w-full max-w-lg p-6 shadow-sm"):
            ui.label("Retrieval").classes("text-sm font-semibold text-slate-600 mb-1")
            ui.label(
                "Number of context chunks passed to the LLM per query."
            ).classes("text-xs text-gray-400 mb-3")
            top_k_inp = (
                ui.number(label="RAG_TOP_K", value=top_k_val, min=1, max=50, step=1, format="%d")
                .classes("w-full")
                .props("outlined dense")
            )

            ui.separator().classes("my-4")

            ui.label("Ingestion").classes("text-sm font-semibold text-slate-600 mb-1")
            ui.label(
                "Text chunk size and overlap used when indexing documents."
            ).classes("text-xs text-gray-400 mb-3")

            with ui.row().classes("w-full gap-4"):
                chunk_size_inp = (
                    ui.number(label="CHUNK_SIZE", value=chunk_size_val, min=100, max=8000, step=100, format="%d")
                    .classes("flex-1")
                    .props("outlined dense")
                )
                chunk_overlap_inp = (
                    ui.number(label="CHUNK_OVERLAP", value=chunk_overlap_val, min=0, max=2000, step=50, format="%d")
                    .classes("flex-1")
                    .props("outlined dense")
                )

            if can_update:
                async def _save() -> None:
                    try:
                        new_top_k = max(1, int(top_k_inp.value or 5))
                        new_chunk_size = max(100, int(chunk_size_inp.value or 1000))
                        new_chunk_overlap = max(0, int(chunk_overlap_inp.value or 200))

                        if new_chunk_overlap >= new_chunk_size:
                            ui.notify("CHUNK_OVERLAP must be smaller than CHUNK_SIZE.", type="warning")
                            return

                        # RAG_TOP_K: persist + apply live
                        await asyncio.to_thread(self.k8s.write, "rag-top-k", str(new_top_k))
                        set_top_k(new_top_k)

                        # CHUNK_SIZE / CHUNK_OVERLAP: patch ingester CronJob env
                        await asyncio.to_thread(
                            self.k8s.patch_cronjob_env,
                            "shokan-core-ingester", "CHUNK_SIZE", str(new_chunk_size),
                        )
                        await asyncio.to_thread(
                            self.k8s.patch_cronjob_env,
                            "shokan-core-ingester", "CHUNK_OVERLAP", str(new_chunk_overlap),
                        )
                        ui.notify("RAG settings saved.", type="positive")
                    except Exception as exc:
                        ui.notify(f"Error: {exc}", type="negative")

                ui.button("Save", icon="save", on_click=_save).props(
                    "unelevated color=primary"
                ).classes("mt-4")
            else:
                top_k_inp.disable()
                chunk_size_inp.disable()
                chunk_overlap_inp.disable()

    # ── Chat Management ────────────────────────────────────────────────────────

    async def _render_chat_management(self, perms: set[str]) -> None:
        can_delete = can(perms, "system:chats:delete")

        ui.label("Chat Management").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label(
            "Disk usage per user. Delete removes all conversation files for that user."
        ).classes("text-sm text-gray-500 mb-3")

        with ui.row().classes("w-full justify-between items-center mb-2"):
            summary_label = ui.label("").classes("text-xs text-slate-400")
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
            with ui.grid(columns=_CHAT_COLS).classes(
                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
            ):
                for col in ["USER", "CHATS", "SIZE", ""]:
                    ui.label(col).classes("font-semibold text-slate-500 text-xs tracking-wider")

            rows_box = ui.column().classes("w-full")

        async def load_rows() -> None:
            rows_box.clear()
            summary_label.set_text("")
            with rows_box:
                ui.spinner(size="sm").classes("mx-auto my-4")

            try:
                user_stats = await asyncio.to_thread(self.storage.list_all_users)
                kc_users   = await self.kc.list_users()
            except Exception as exc:
                rows_box.clear()
                with rows_box:
                    ui.label(f"Error: {exc}").classes("text-red-500 text-sm px-4 py-3")
                return

            id_to_kc: dict[str, dict] = {u["id"]: u for u in kc_users}

            rows_box.clear()
            if not user_stats:
                with rows_box:
                    ui.label("No conversations stored yet.").classes(
                        "text-slate-400 text-sm italic px-4 py-3"
                    )
                return

            total_chats = sum(u["chat_count"] for u in user_stats)
            total_size  = sum(u["total_size_bytes"] for u in user_stats)
            summary_label.set_text(
                f"{len(user_stats)} users · {total_chats} conversations · {_fmt_size(total_size)}"
            )

            with rows_box:
                for entry in user_stats:
                    uid  = entry["user_id"]
                    kc   = id_to_kc.get(uid, {})
                    name = kc.get("username") or uid[:12] + "…"
                    email = kc.get("email", "")

                    with ui.grid(columns=_CHAT_COLS).classes(
                        "w-full items-center px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                    ):
                        with ui.column().classes("gap-0 min-w-0"):
                            ui.label(name).classes("text-sm font-medium text-slate-700 truncate")
                            if email:
                                ui.label(email).classes("text-xs text-slate-400 truncate")
                            ui.label(uid).classes("text-xs text-slate-300 font-mono truncate")

                        ui.label(str(entry["chat_count"])).classes("text-sm text-slate-600")
                        ui.label(_fmt_size(entry["total_size_bytes"])).classes("text-sm text-slate-600")

                        with ui.row().classes("justify-end"):
                            if can_delete:
                                ui.button(
                                    icon="delete_forever",
                                    on_click=lambda uid=uid, uname=name: _confirm_delete(uid, uname),
                                ).props("flat round dense color=negative").tooltip("Delete all conversations")

        def _confirm_delete(uid: str, uname: str) -> None:
            with ui.dialog() as dlg, ui.card().classes("w-96 p-6 gap-4"):
                ui.label("Delete conversations").classes("text-lg font-bold text-slate-800")
                ui.label(
                    f"This will permanently delete all conversations for user «{uname}». "
                    "This action cannot be undone."
                ).classes("text-sm text-slate-600")
                with ui.row().classes("justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")

                    async def _do_delete(d=dlg, u=uid, n=uname) -> None:
                        try:
                            count = await asyncio.to_thread(self.storage.delete_all_chats, u)
                            ui.notify(
                                f"Deleted {count} conversation(s) for {n}.",
                                type="positive",
                            )
                            d.close()
                            await load_rows()
                        except Exception as exc:
                            ui.notify(f"Error: {exc}", type="negative")

                    ui.button(
                        "Delete", on_click=_do_delete
                    ).props("unelevated color=negative")
            dlg.open()

        refresh_btn.on("click", load_rows)
        asyncio.ensure_future(load_rows())

    # ── Dashboard ──────────────────────────────────────────────────────────────

    async def _render_dashboard(self) -> None:
        ui.label("Dashboard").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label("Aggregated usage across all conversations and users.").classes(
            "text-sm text-gray-500 mb-4"
        )

        with ui.row().classes("w-full justify-end mb-2"):
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")

        stats_area = ui.column().classes("w-full gap-4")

        async def load_dashboard() -> None:
            stats_area.clear()
            with stats_area:
                ui.spinner(size="sm").classes("mx-auto my-4")

            stats = await asyncio.to_thread(self.audit.stats)
            stats_area.clear()

            with stats_area:
                # ── Summary cards ──────────────────────────────────────────────
                with ui.row().classes("w-full gap-4 flex-wrap"):
                    for label, value, icon, color in [
                        ("Total Queries", str(stats["total"]),          "chat",        "blue"),
                        ("Active Users",  str(len(stats["by_user"])),   "group",       "green"),
                        ("Models Used",   str(len(stats["by_model"])),  "smart_toy",   "purple"),
                    ]:
                        with ui.card().classes("flex-1 min-w-36 p-4 shadow-sm"):
                            with ui.row().classes("items-center gap-2 mb-1"):
                                ui.icon(icon, size="sm").classes(f"text-{color}-500")
                                ui.label(label).classes("text-xs text-slate-500 font-medium")
                            ui.label(value).classes("text-2xl font-bold text-slate-800")

                if not stats["total"]:
                    ui.label("No query data yet — data accumulates as users chat.").classes(
                        "text-slate-400 text-sm italic p-4"
                    )
                    return

                # ── Queries per day bar chart ──────────────────────────────────
                with ui.card().classes("w-full p-4 shadow-sm"):
                    ui.label("Queries per day (last 30 days)").classes(
                        "text-sm font-semibold text-slate-600 mb-3"
                    )
                    by_day = stats["by_day"]
                    cutoff = (
                        datetime.datetime.now() - datetime.timedelta(days=30)
                    ).strftime("%Y-%m-%d")
                    days   = [d for d in by_day if d >= cutoff]
                    counts = [by_day[d] for d in days]

                    if days:
                        ui.echart({
                            "tooltip": {"trigger": "axis"},
                            "xAxis": {
                                "type": "category",
                                "data": days,
                                "axisLabel": {"rotate": 45, "fontSize": 10},
                            },
                            "yAxis": {"type": "value", "minInterval": 1},
                            "series": [{
                                "type": "bar",
                                "data": counts,
                                "itemStyle": {"color": "#3b82f6"},
                                "barMaxWidth": 40,
                            }],
                            "grid": {"left": "5%", "right": "5%", "bottom": "20%", "top": "10%"},
                        }).classes("w-full h-48")
                    else:
                        ui.label("No data in the last 30 days.").classes(
                            "text-slate-400 text-sm italic"
                        )

                with ui.row().classes("w-full gap-4"):
                    # ── Queries by model pie chart ─────────────────────────────
                    with ui.card().classes("flex-1 p-4 shadow-sm"):
                        ui.label("Queries by model").classes(
                            "text-sm font-semibold text-slate-600 mb-3"
                        )
                        by_model = stats["by_model"]
                        if by_model:
                            pie_data = [
                                {"name": k, "value": v}
                                for k, v in list(by_model.items())[:10]
                            ]
                            ui.echart({
                                "tooltip": {"trigger": "item"},
                                "series": [{
                                    "type": "pie",
                                    "radius": ["40%", "70%"],
                                    "data": pie_data,
                                    "label": {"fontSize": 10},
                                }],
                            }).classes("w-full h-48")
                        else:
                            ui.label("No model data.").classes("text-slate-400 text-sm italic")

                    # ── Most active users table ────────────────────────────────
                    with ui.card().classes("flex-1 p-4 shadow-sm"):
                        ui.label("Most active users").classes(
                            "text-sm font-semibold text-slate-600 mb-3"
                        )
                        by_user = stats["by_user"]
                        if by_user:
                            try:
                                kc_users = await self.kc.list_users()
                                id_to_name = {
                                    u["id"]: u.get("username", u["id"][:12])
                                    for u in kc_users
                                }
                            except Exception:
                                id_to_name = {}
                            with ui.column().classes("w-full gap-1"):
                                for uid, count in list(by_user.items())[:8]:
                                    name = id_to_name.get(uid, uid[:16] + "…")
                                    with ui.row().classes(
                                        "w-full justify-between items-center py-1 border-b border-slate-100"
                                    ):
                                        ui.label(name).classes(
                                            "text-sm text-slate-700 truncate"
                                        )
                                        ui.label(str(count)).classes(
                                            "text-sm font-mono text-slate-500 shrink-0"
                                        )
                        else:
                            ui.label("No user data.").classes("text-slate-400 text-sm italic")

        refresh_btn.on("click", load_dashboard)
        asyncio.ensure_future(load_dashboard())

    # ── Audit Log ──────────────────────────────────────────────────────────────

    def _render_audit_log(self) -> None:
        ui.label("Audit Log").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label(
            "Recent platform activity. Events are stored locally and streamed to stdout."
        ).classes("text-sm text-gray-500 mb-3")

        _ACTION_COLORS = {
            "chat":      "text-blue-600",
            "tool_call": "text-purple-600",
        }

        with ui.row().classes("w-full items-center gap-2 mb-3"):
            action_filter = ui.select(
                {"": "All actions", "chat": "Chat", "tool_call": "Tool calls"},
                value="",
                label="Filter",
            ).props("dense outlined").classes("w-40")
            refresh_btn = ui.button("Refresh", icon="refresh").props("outline dense")
            summary_label = ui.label("").classes("text-xs text-slate-400 ml-auto")

        with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
            with ui.grid(columns=_AUDIT_COLS).classes(
                "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
            ):
                for col in ["TIME", "USER", "ACTION", "RESOURCE", "DETAILS"]:
                    ui.label(col).classes(
                        "font-semibold text-slate-500 text-xs tracking-wider"
                    )
            rows_box = ui.column().classes("w-full")

        def load_rows() -> None:
            rows_box.clear()
            action = action_filter.value or None
            events = self.audit.recent(limit=200, action=action)
            summary_label.set_text(f"{len(events)} events")

            if not events:
                with rows_box:
                    ui.label("No audit events found.").classes(
                        "text-slate-400 text-sm italic px-4 py-3"
                    )
                return

            with rows_box:
                for ev in events:
                    ts_str = datetime.datetime.fromtimestamp(ev["ts"]).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    action_str = ev.get("action", "")
                    details_str = ", ".join(
                        f"{k}={v}"
                        for k, v in (ev.get("details") or {}).items()
                    )
                    uid = ev.get("user_id", "")
                    res = ev.get("resource", "")
                    with ui.grid(columns=_AUDIT_COLS).classes(
                        "w-full items-start px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                    ):
                        ui.label(ts_str).classes("font-mono text-xs text-slate-500")
                        ui.label(
                            uid[:20] + ("…" if len(uid) > 20 else "")
                        ).classes("text-xs text-slate-600 font-mono truncate")
                        ui.label(action_str).classes(
                            f"text-xs font-semibold {_ACTION_COLORS.get(action_str, 'text-slate-600')}"
                        )
                        ui.label(
                            res[:30] + ("…" if len(res) > 30 else "")
                        ).classes("text-xs text-slate-600 truncate")
                        ui.label(details_str or "—").classes(
                            "text-xs text-slate-400 truncate"
                        )

        action_filter.on("update:modelValue", lambda _: load_rows())
        refresh_btn.on("click", load_rows)
        load_rows()

    # ── Export ─────────────────────────────────────────────────────────────────

    async def _render_export_tab(self) -> None:
        ui.label("Export").classes("text-lg font-semibold text-slate-800 mb-1")
        ui.label("Select what to include in the export file.").classes(
            "text-sm text-gray-500 mb-4"
        )

        export_checks: dict[str, ui.checkbox] = {}
        for key, meta in SECTIONS.items():
            with ui.row().classes("items-center gap-3 mb-2"):
                cb = ui.checkbox(meta["label"], value=True)
                export_checks[key] = cb
                if meta["warning"]:
                    ui.icon("warning", size="xs").classes("text-amber-500").tooltip(
                        meta["warning"]
                    )

        export_status = ui.label("").classes("text-xs text-slate-400 mt-2")

        async def _do_export() -> None:
            selected = [k for k, cb in export_checks.items() if cb.value]
            if not selected:
                ui.notify("Select at least one section.", type="warning")
                return
            export_status.set_text("Building export…")
            try:
                bundle = await self.export_svc.export(selected)
                data = json.dumps(bundle, ensure_ascii=False, indent=2)
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                ui.download(data.encode("utf-8"), f"shokan-export-{ts}.json")
                export_status.set_text(
                    f"Exported {len(selected)} section(s) — {len(data) // 1024} KB"
                )
            except Exception as exc:
                export_status.set_text(f"Error: {exc}")
                ui.notify(f"Export failed: {exc}", type="negative")

        ui.button("Export selected", icon="download", on_click=_do_export).props(
            "unelevated color=primary"
        ).classes("mt-3")

    # ── Import ─────────────────────────────────────────────────────────────────

    def _render_import_tab(self) -> None:
        ui.label("Import").classes("text-lg font-semibold text-slate-800 mb-1")

        with ui.row().classes("items-start gap-2 mb-4 bg-amber-50 rounded p-3"):
            ui.icon("warning", size="sm").classes("text-amber-500 shrink-0 mt-0.5")
            ui.label(
                "Importing overwrites existing data in each selected section. "
                "This cannot be undone. Export first if you want a backup."
            ).classes("text-xs text-amber-700")

        parsed: dict = {"bundle": None}
        import_checks: dict = {}
        import_area = ui.column().classes("w-full gap-3 mt-4")

        def _on_upload(e) -> None:
            try:
                bundle = json.loads(e.content.read())
            except Exception as exc:
                ui.notify(f"Invalid file: {exc}", type="negative")
                return
            if bundle.get("shokan_export_version") is None:
                ui.notify("File does not appear to be a Shokan export.", type="warning")
                return
            parsed["bundle"] = bundle
            _render_import_panel(bundle)

        ui.upload(
            label="Drop a shokan-export-*.json file here or click to browse",
            on_upload=_on_upload,
            auto_upload=True,
        ).props("accept=.json").classes("w-full")

        def _render_import_panel(bundle: dict) -> None:
            import_area.clear()
            import_checks.clear()
            summary = self.export_svc.section_summary(bundle)
            exported_at = bundle.get("exported_at", "unknown")

            with import_area:
                ui.label(
                    f"Exported: {exported_at}  ·  {len(summary)} section(s) detected"
                ).classes("text-xs text-slate-400")

                for section, count in summary.items():
                    meta = SECTIONS.get(section, {"label": section, "icon": "help", "warning": None})
                    with ui.row().classes("items-center gap-3"):
                        cb = ui.checkbox(
                            f"{meta['label']} ({count} item{'s' if count != 1 else ''})",
                            value=(section != "permissions"),
                        )
                        import_checks[section] = cb
                        if meta.get("warning"):
                            ui.icon("warning", size="xs").classes("text-amber-500").tooltip(
                                meta["warning"]
                            )

                import_status = ui.label("").classes("text-xs text-slate-400 mt-1")

                async def _do_import() -> None:
                    b = parsed["bundle"]
                    if not b:
                        return
                    selected = [k for k, cb in import_checks.items() if cb.value]
                    if not selected:
                        ui.notify("Select at least one section.", type="warning")
                        return
                    import_status.set_text("Importing…")
                    results = await self.export_svc.import_bundle(b, sections=selected)
                    import_area.clear()
                    with import_area:
                        ui.label("Import results:").classes(
                            "text-sm font-semibold text-slate-700 mb-2"
                        )
                        for section, res in results.items():
                            m = SECTIONS.get(section, {"label": section})
                            if res["ok"]:
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon("check_circle", size="sm").classes("text-green-500")
                                    ui.label(
                                        f"{m['label']}: {res['count']} item(s) imported"
                                    ).classes("text-sm text-slate-700")
                            else:
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon("error", size="sm").classes("text-red-500")
                                    ui.label(
                                        f"{m['label']}: {res['error']}"
                                    ).classes("text-sm text-red-600")

                ui.button("Import selected", icon="upload", on_click=_do_import).props(
                    "unelevated color=negative"
                ).classes("mt-2")
