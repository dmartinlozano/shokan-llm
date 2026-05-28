import asyncio
from typing import Callable, Dict, List, Optional

from nicegui import context as ng_context
from nicegui import ui


class CRUDTemplate:
    """
    Reusable CRUD table widget for NiceGUI pages.

    Features: in-memory search, sortable text columns, pagination.

    fields: optional list of field specs for the create/edit modal.
        Each spec: {key, label, type?, options?, placeholder?, required?}
        type: "input" (default) | "select" | "password" | "textarea" | "number"
        Keys starting with "_" are treated as hidden data (not shown in modal
        or table, but passed through to callbacks).

    columns: names shown as table headers. Keys in data dicts are derived as
        col.lower().replace(" ", "_").

    on_new / on_edit / on_delete are all optional.
    on_new_click: bypasses modal entirely when "New" is clicked.
    direct_edit: edit button calls on_edit(item) directly (toggle-style actions).

    edit_icon / edit_icon_field: icon name or per-row field name to read it from.
    edit_class / edit_class_field: CSS class(es) or per-row field name.
    edit_tooltip_field: per-row field name for the button tooltip.
    """

    def __init__(
        self,
        title: str,
        columns: List[str],
        on_refresh: Callable,
        on_new: Optional[Callable] = None,
        on_new_click: Optional[Callable] = None,
        on_edit: Optional[Callable] = None,
        on_delete: Optional[Callable] = None,
        fields: Optional[List[Dict]] = None,
        direct_edit: bool = False,
        direct_edit_refresh: bool = True,
        edit_icon: str = "edit",
        edit_icon_field: Optional[str] = None,
        edit_class: str = "",
        edit_class_field: Optional[str] = None,
        edit_tooltip_field: Optional[str] = None,
    ):
        self.title = title
        self.columns = columns
        action_cols = sum([
            1 if on_edit is not None else 0,
            1 if on_delete is not None else 0,
        ])
        self.grid_cols = len(columns) + (1 if action_cols else 0)
        self.on_refresh_callback = on_refresh
        self.on_new_callback = on_new
        self.on_new_click = on_new_click
        self.on_edit_callback = on_edit
        self.on_delete_callback = on_delete
        self.fields = fields
        self.direct_edit = direct_edit
        self.direct_edit_refresh = direct_edit_refresh
        self.edit_icon = edit_icon
        self.edit_icon_field = edit_icon_field
        self.edit_class = edit_class
        self.edit_class_field = edit_class_field
        self.edit_tooltip_field = edit_tooltip_field

        # Capture page slot at render time so dialogs are anchored to the
        # layout root rather than the button's parent container.
        self._page_slot = ng_context.client.layout.default_slot

        # Search / sort / pagination state
        self._all_data: list = []
        self._search_text: str = ""
        self._sort_col: Optional[str] = None
        self._sort_asc: bool = True
        self._page: int = 0
        self._page_size: int = 10
        self._nonsortable_cols: set = set()  # list/bool columns — no sort

        # UI refs
        self.table_container: Optional[ui.column] = None
        self._sort_icons: Dict[str, ui.icon] = {}
        self._pagination_label: Optional[ui.label] = None
        self._prev_btn = None
        self._next_btn = None

        self._build_ui()

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _col_key(col: str) -> str:
        return col.lower().replace(" ", "_")

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        with ui.column().classes("w-full gap-2"):
            # Title + action buttons
            with ui.row().classes("w-full justify-between items-center"):
                ui.label(self.title).classes("text-lg font-semibold text-slate-800")
                with ui.row().classes("gap-2"):
                    ui.button("Refresh", icon="refresh", on_click=self.refresh).props("outline dense")
                    if self.on_new_click is not None:
                        ui.button("New", icon="add", on_click=self.on_new_click).props("unelevated dense color=primary")
                    elif self.on_new_callback is not None:
                        ui.button("New", icon="add", on_click=lambda: self.open_modal()).props("unelevated dense color=primary")

            # Search bar
            search_input = (
                ui.input(placeholder="Search in table…")
                .props("outlined dense clearable")
                .classes("w-full")
            )
            search_input.on("update:modelValue", lambda e: self._on_search(e.args))

            # Table
            with ui.card().classes("w-full p-0 overflow-hidden shadow-sm"):
                # Column headers
                with ui.grid(columns=self.grid_cols).classes(
                    "w-full bg-slate-100 px-4 py-2 border-b border-slate-200"
                ):
                    for col in self.columns:
                        key = self._col_key(col)
                        with ui.row().classes("items-center gap-1 select-none").style("cursor:pointer") as hdr:
                            ui.label(col.upper()).classes("font-semibold text-slate-500 text-xs tracking-wider")
                            icon = ui.icon("unfold_more", size="xs").classes("text-slate-300")
                            self._sort_icons[key] = icon
                        hdr.on("click", lambda k=key: self._on_sort_click(k))
                    if self.on_edit_callback is not None or self.on_delete_callback is not None:
                        ui.label("").classes("text-xs")

                self.table_container = ui.column().classes("w-full")

            # Pagination row
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-1"):
                    self._prev_btn = ui.button(
                        icon="chevron_left", on_click=self._prev_page
                    ).props("flat round dense")
                    self._pagination_label = ui.label("").classes("text-xs text-slate-500 min-w-24 text-center")
                    self._next_btn = ui.button(
                        icon="chevron_right", on_click=self._next_page
                    ).props("flat round dense")
                with ui.row().classes("items-center gap-1"):
                    ui.label("Rows:").classes("text-xs text-slate-400")
                    ui.select(
                        [10, 25, 50],
                        value=self._page_size,
                        on_change=lambda e: self._on_page_size(e.value),
                    ).props("outlined dense").classes("w-16 text-xs")

        self.refresh()

    # ── Search / sort / page handlers ──────────────────────────────────────────

    def _on_search(self, text) -> None:
        self._search_text = (text or "").strip().lower()
        self._page = 0
        self._render_view()

    def _on_sort_click(self, col_key: str) -> None:
        if col_key in self._nonsortable_cols:
            return
        if self._sort_col == col_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_key
            self._sort_asc = True
        self._page = 0
        self._update_sort_icons()
        self._render_view()

    def _on_page_size(self, size: int) -> None:
        self._page_size = size
        self._page = 0
        self._render_view()

    def _prev_page(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render_view()

    def _next_page(self) -> None:
        total = len(self._get_filtered_sorted())
        max_page = max(0, (total - 1) // self._page_size) if total else 0
        if self._page < max_page:
            self._page += 1
            self._render_view()

    def _update_sort_icons(self) -> None:
        for key, icon in self._sort_icons.items():
            if key in self._nonsortable_cols:
                continue
            if key == self._sort_col:
                icon._props["name"] = "arrow_upward" if self._sort_asc else "arrow_downward"
                icon.classes(add="text-blue-500", remove="text-slate-300")
            else:
                icon._props["name"] = "unfold_more"
                icon.classes(add="text-slate-300", remove="text-blue-500")
            icon.update()

    # ── Data pipeline ──────────────────────────────────────────────────────────

    def _detect_nonsortable(self, data: list) -> None:
        if not data:
            return
        first = data[0]
        for col in self.columns:
            key = self._col_key(col)
            val = first.get(key)
            if isinstance(val, (list, bool)):
                self._nonsortable_cols.add(key)
                if key in self._sort_icons:
                    self._sort_icons[key].set_visibility(False)

    def _get_filtered_sorted(self) -> list:
        data = self._all_data

        if self._search_text:
            q = self._search_text
            data = [
                row for row in data
                if any(
                    q in str(row.get(self._col_key(col), "")).lower()
                    for col in self.columns
                    if self._col_key(col) not in self._nonsortable_cols
                )
            ]

        if self._sort_col and self._sort_col not in self._nonsortable_cols:
            data = sorted(
                data,
                key=lambda r: str(r.get(self._sort_col, "")).lower(),
                reverse=not self._sort_asc,
            )

        return data

    def _render_view(self) -> None:
        filtered = self._get_filtered_sorted()
        total = len(filtered)
        start = self._page * self._page_size
        end = min(start + self._page_size, total)
        self._populate(filtered[start:end])

        if self._pagination_label:
            if total == 0:
                self._pagination_label.set_text("No items" if not self._search_text else "No results")
            else:
                self._pagination_label.set_text(f"{start + 1}–{end} of {total}")

        if self._prev_btn:
            self._prev_btn.set_enabled(self._page > 0)
        if self._next_btn:
            max_page = max(0, (total - 1) // self._page_size) if total else 0
            self._next_btn.set_enabled(self._page < max_page)

    # ── Data loading ───────────────────────────────────────────────────────────

    def refresh(self) -> None:
        result = self.on_refresh_callback()
        if asyncio.iscoroutine(result):
            asyncio.ensure_future(self._async_load(result))
        else:
            self._all_data = result or []
            self._detect_nonsortable(self._all_data)
            self._render_view()

    async def _async_load(self, coro) -> None:
        try:
            data = await coro
        except Exception as exc:
            self.table_container.clear()
            with self.table_container:
                ui.label(f"Error loading: {exc}").classes("text-red-500 text-sm px-4 py-3")
            return
        self._all_data = data or []
        self._detect_nonsortable(self._all_data)
        self._render_view()

    def _populate(self, data: list) -> None:
        self.table_container.clear()
        if not data:
            with self.table_container:
                msg = "No items." if not self._all_data else "No results."
                ui.label(msg).classes("text-slate-400 text-sm italic px-4 py-3")
            return
        for item in data:
            with self.table_container:
                with ui.grid(columns=self.grid_cols).classes(
                    "w-full items-center px-4 py-2 border-b border-slate-100 hover:bg-slate-50"
                ):
                    for col in self.columns:
                        key = self._col_key(col)
                        val = item.get(key, "")
                        if isinstance(val, list):
                            if val:
                                ui.select(val, value=val[0]).props("outlined dense").classes("text-xs")
                            else:
                                ui.label("—").classes("text-slate-400 text-sm")
                        elif isinstance(val, bool):
                            ui.checkbox(value=val).props("dense")
                        else:
                            ui.label(str(val) if val else "—").classes("text-slate-700 text-sm truncate")

                    if self.on_edit_callback is not None or self.on_delete_callback is not None:
                        with ui.row().classes("gap-1 justify-end"):
                            if self.on_edit_callback is not None:
                                icon = item.get(self.edit_icon_field, self.edit_icon) if self.edit_icon_field else self.edit_icon
                                cls = item.get(self.edit_class_field, self.edit_class) if self.edit_class_field else self.edit_class
                                tooltip = item.get(self.edit_tooltip_field, "Edit") if self.edit_tooltip_field else "Edit"
                                if self.direct_edit:
                                    async def _direct_click(i=item):
                                        try:
                                            coro = self.on_edit_callback(i)
                                            if asyncio.iscoroutine(coro):
                                                await coro
                                            if self.direct_edit_refresh:
                                                self.refresh()
                                        except Exception as exc:
                                            ui.notify(f"Error: {exc}", type="negative")
                                    btn = ui.button(icon=icon, on_click=_direct_click).props("flat round dense color=primary")
                                else:
                                    btn = ui.button(icon=icon, on_click=lambda i=item: self.open_modal(i)).props("flat round dense color=primary")
                                if cls:
                                    btn.classes(cls)
                                btn.tooltip(tooltip)
                            if self.on_delete_callback is not None:
                                ui.button(icon="delete", on_click=lambda i=item: self._confirm_delete(i)).props(
                                    "flat round dense color=negative"
                                )

    # ── Modal ──────────────────────────────────────────────────────────────────

    def open_modal(self, item: Dict = None) -> None:
        is_edit = item is not None
        modal_fields = self.fields or [
            {"key": self._col_key(col), "label": col} for col in self.columns
        ]
        with self._page_slot:
            with ui.dialog() as dialog, ui.card().classes("w-[500px] p-6 gap-4"):
                ui.label("Edit" if is_edit else "New").classes("text-xl font-bold text-slate-800")
                inputs: Dict[str, ui.element] = {}
                with ui.column().classes("w-full gap-3"):
                    for field in modal_fields:
                        key = field["key"]
                        if key.startswith("_"):
                            continue
                        label = field.get("label", key)
                        ftype = field.get("type", "input")
                        options = field.get("options", [])
                        placeholder = field.get("placeholder", "")
                        initial = item.get(key, "") if is_edit else ""

                        if ftype == "select":
                            val = initial if initial in options else (list(options)[0] if options else "")
                            inputs[key] = ui.select(options, value=val, label=label).classes("w-full").props("outlined dense")
                        elif ftype == "password":
                            ph = placeholder or ("leave blank to keep" if is_edit else "")
                            inputs[key] = ui.input(label=label, password=True, placeholder=ph).classes("w-full").props("outlined dense")
                        elif ftype == "textarea":
                            inputs[key] = ui.textarea(label=label, value=str(initial)).classes("w-full").props("outlined rows=3")
                        elif ftype == "number":
                            inputs[key] = ui.number(label=label, value=float(initial) if initial else 0).classes("w-full").props("outlined dense")
                        else:
                            inputs[key] = ui.input(
                                label=label,
                                value=str(initial) if initial else "",
                                placeholder=placeholder,
                            ).classes("w-full").props("outlined dense")

                with ui.row().classes("justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")

                    async def _save(d=dialog, inp=inputs, orig=item):
                        new_data = {k: v.value for k, v in inp.items()}
                        try:
                            coro = (
                                self.on_edit_callback(orig, new_data)
                                if orig is not None
                                else self.on_new_callback(new_data)
                            )
                            if asyncio.iscoroutine(coro):
                                await coro
                            d.close()
                            self.refresh()
                        except Exception as exc:
                            ui.notify(f"Error: {exc}", type="negative")

                    ui.button("Save", on_click=_save).props("unelevated color=primary")
        dialog.open()

    # ── Delete ─────────────────────────────────────────────────────────────────

    def _confirm_delete(self, item: dict) -> None:
        with self._page_slot:
            with ui.dialog() as dlg, ui.card().classes("p-6 gap-4"):
                ui.label("Delete this item?").classes("text-slate-700 font-medium")
                with ui.row().classes("gap-2 justify-end"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")

                    async def _delete(d=dlg, i=item):
                        d.close()
                        try:
                            coro = self.on_delete_callback(i)
                            if asyncio.iscoroutine(coro):
                                await coro
                            self.refresh()
                        except Exception as exc:
                            ui.notify(f"Error: {exc}", type="negative")

                    ui.button("Delete", on_click=_delete).props("unelevated color=negative")
        dlg.open()
