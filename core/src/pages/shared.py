"""Shared UI helpers for all permissions sub-pages."""

from nicegui import ui

from connectors.openfga import OpenFGA

_ROLE_COLORS: dict[str, str] = {
    "admin":        "red",
    "member":       "blue",
    "allowed_user": "teal",
    "owner":        "orange",
    "viewer":       "cyan",
}


def role_color(role: str) -> str:
    return _ROLE_COLORS.get(role, "grey")


def row_header(columns: list[str]) -> None:
    with ui.row().classes("w-full text-xs font-semibold text-gray-400 uppercase px-2 pb-1 border-b gap-2"):
        for col in columns:
            ui.label(col).classes("flex-1")


def principal_crud_row(
    fga: OpenFGA,
    subject: str,
    name: str,
    sublabel: str,
    icon_name: str,
    current_role: str,
    roles: tuple,
    object_id: str,
    refresh_fn,
) -> None:
    _all = ["none", *roles]
    with ui.row().classes("w-full items-center px-2 py-1 hover:bg-gray-50 rounded gap-2"):
        with ui.row().classes("flex-1 items-center gap-1 min-w-0"):
            ui.icon(icon_name, size="xs").classes("text-gray-400 shrink-0")
            ui.label(name).classes("font-mono text-sm truncate")
        ui.label(sublabel).classes("flex-1 text-sm text-gray-400 truncate")
        role_sel = ui.select(
            _all,
            value=current_role if current_role in _all else "none",
        ).props("dense outlined").classes("w-28")

        def make_save(subj=subject, rs=role_sel, old=current_role):
            async def save() -> None:
                new = rs.value
                prev = old if old not in ("none", "") else None
                if new == "none":
                    if prev:
                        await fga.remove_relation(subj, prev, object_id)
                        ui.notify("Role removed", type="info")
                        await refresh_fn()
                    return
                await fga.set_relation(subj, new, prev, object_id)
                ui.notify(f"Role set to {new}", type="positive")
                await refresh_fn()
            return save

        ui.button(icon="save", on_click=make_save()).props("flat round dense").classes("text-blue-500")


def _make_remove_role(fga: OpenFGA, subject: str, current_role: str, object_id: str, refresh_fn):
    async def handler() -> None:
        if current_role and current_role != "none":
            await fga.remove_relation(subject, current_role, object_id)
        await refresh_fn()
        ui.notify("Role removed", type="info")
    return handler


async def fga_access_card(
    fga: OpenFGA,
    title: str,
    icon: str,
    object_id: str,
    roles: tuple,
    principals: dict[str, str] | None = None,
) -> None:
    principal_options: dict[str, str] = principals if principals else {}

    with ui.expansion(title, icon=icon).classes("w-full mb-1"):

        ui.label("Grant access").classes("text-xs font-semibold text-gray-500 mt-1 px-2")
        with ui.row().classes("gap-2 px-2 mb-2 items-end flex-wrap"):
            if principal_options:
                subj_sel = ui.select(
                    options=principal_options,
                    label="User / Group",
                    with_input=True,
                ).classes("flex-1 min-w-48")
            else:
                subj_sel = ui.input("user:<id> or group:<id>#member").classes("flex-1")
            role_sel = ui.select(list(roles), value=roles[0], label="Role").classes("w-32")

        ui.separator().classes("mb-1")
        box = ui.column().classes("w-full gap-1")

        async def refresh_access() -> None:
            box.clear()
            try:
                tuples = await fga.get_object_tuples(object_id)
            except Exception as exc:
                with box:
                    ui.label(f"Error loading: {exc}").classes("text-sm text-red-500 px-2")
                return
            grants = {s: r for s, r in tuples.items() if r in roles}
            with box:
                if not grants:
                    ui.label("No access grants yet.").classes("text-sm text-gray-400 italic px-2 py-1")
                    return
                row_header(["Subject", "Role", ""])
                for subj, rel in grants.items():
                    display = (principals or {}).get(subj, subj)

                    def make_role_handler(s=subj, prev=rel):
                        async def on_change(e) -> None:
                            try:
                                await fga.set_relation(s, e.value, prev, object_id)
                                ui.notify(f"Role updated to {e.value}", type="positive")
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")
                        return on_change

                    def make_remove(s=subj, r=rel):
                        async def _remove() -> None:
                            try:
                                await fga.remove_relation(s, r, object_id)
                                await refresh_access()
                                ui.notify("Access removed", type="info")
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")
                        return _remove

                    with ui.row().classes("w-full items-center px-2 py-1 hover:bg-gray-50 rounded gap-2"):
                        with ui.row().classes("flex-1 items-center gap-1 min-w-0"):
                            ui.icon("group" if "#member" in subj else "person", size="xs").classes(
                                "text-gray-400 shrink-0"
                            )
                            ui.label(display).classes("font-mono text-sm truncate")
                        ui.select(
                            list(roles),
                            value=rel,
                            on_change=make_role_handler(),
                        ).props("dense outlined").classes("w-32")
                        ui.button(
                            icon="delete_outline",
                            on_click=make_remove(),
                        ).props("flat round dense").classes("text-red-400")

        async def grant() -> None:
            subj = subj_sel.value
            if not subj:
                ui.notify("Select a user or group", type="warning")
                return
            try:
                await fga.set_relation(subj, role_sel.value, None, object_id)
                subj_sel.value = None
                await refresh_access()
                ui.notify("Access granted", type="positive")
            except Exception as exc:
                ui.notify(f"Error: {exc}", type="negative")

        with ui.row().classes("gap-2 px-2 mt-1"):
            ui.button("Grant", on_click=grant, icon="add").props("flat dense")
            ui.button("Refresh", on_click=refresh_access, icon="refresh").props("flat dense")

        await refresh_access()
