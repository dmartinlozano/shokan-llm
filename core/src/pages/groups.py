"""Platform-level group permissions tab — CRUD with multi-role assignment."""

from nicegui import ui

from templates.crud_template import CRUDTemplate
from connectors.openfga import OpenFGA
from services.groups import GroupService

_PLATFORM_ROLES = ["admin", "member"]


class GroupsPermissions:
    def __init__(self, fga: OpenFGA, group_service: GroupService) -> None:
        self.fga = fga
        self.group_service = group_service

    async def render(self, principals: dict[str, str], user: dict) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        crud: list[CRUDTemplate] = []

        async def refresh_data():
            return await self.group_service.list_with_roles()

        async def on_delete(item: dict):
            try:
                await self.group_service.delete(item["_id"])
            except Exception as exc:
                ui.notify(f"Could not delete group: {exc}", type="negative", timeout=8000)
                return
            ui.notify("Group deleted", type="info")
            if crud:
                crud[0].refresh()

        def open_group_modal(item: dict | None = None) -> None:
            is_edit      = item is not None
            initial_name  = item.get("name", "") if is_edit else ""
            initial_roles = item.get("roles", [_PLATFORM_ROLES[0]]) if is_edit else [_PLATFORM_ROLES[0]]
            group_id      = item.get("_id", "") if is_edit else ""

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[480px] p-6 gap-4"):
                    ui.label("Edit group" if is_edit else "New group").classes("text-xl font-bold text-slate-800")

                    with ui.column().classes("w-full gap-3"):
                        name_inp = (
                            ui.input(label="Group name", value=initial_name)
                            .classes("w-full")
                            .props("outlined dense")
                        )
                        roles_sel = (
                            ui.select(
                                _PLATFORM_ROLES,
                                value=initial_roles,
                                label="Roles",
                                multiple=True,
                            )
                            .classes("w-full")
                            .props("outlined dense use-chips")
                        )

                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def save(d=dlg):
                            name  = name_inp.value.strip()
                            roles = roles_sel.value if isinstance(roles_sel.value, list) else (
                                [roles_sel.value] if roles_sel.value else []
                            )

                            if not name:
                                ui.notify("Group name is required.", type="warning")
                                return
                            if not roles:
                                ui.notify("At least one role is required.", type="warning")
                                return

                            try:
                                if is_edit:
                                    await self.group_service.update(group_id, name, roles)
                                else:
                                    await self.group_service.create(name, roles)
                            except Exception as exc:
                                ui.notify(f"Could not save group: {exc}", type="negative", timeout=8000)
                                return

                            ui.notify("Group saved.", type="positive")
                            d.close()
                            if crud:
                                crud[0].refresh()

                        ui.button("Save", on_click=save).props("unelevated color=primary")

            dlg.open()

        tpl = CRUDTemplate(
            title="Groups",
            columns=["Name", "Roles"],
            on_refresh=refresh_data,
            on_new_click=lambda: open_group_modal(),
            on_edit=lambda item: open_group_modal(item),
            on_delete=on_delete,
            direct_edit=True,
        )
        crud.append(tpl)
