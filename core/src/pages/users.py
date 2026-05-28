"""Platform-level user permissions tab — CRUD via CRUDTemplate."""

from nicegui import ui

from templates.crud_template import CRUDTemplate
from templates.permission_picker import render_permission_picker
from connectors.mcp import SERVERS as MCP_SERVERS
from connectors.openfga import OpenFGA, SHOKAN_OBJECT
from services.permissions import UIPermService
from services.users import UserService

_PLATFORM_ROLES = ["admin", "member"]

_MCP_LABELS = {
    "git": "Git", "jira": "Jira", "confluence": "Confluence",
    "slack": "Slack", "gmail": "Gmail", "discord": "Discord",
}


class UsersPermissions:
    def __init__(self, fga: OpenFGA, user_service: UserService, ui_perm_svc: UIPermService | None = None) -> None:
        self.fga = fga
        self.user_service = user_service
        self.ui_perm_svc = ui_perm_svc

    async def render(self, principals: dict[str, str], user: dict) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        crud: list[CRUDTemplate] = []

        async def refresh_data():
            return await self.user_service.list_with_roles()

        async def on_delete(item: dict):
            try:
                await self.user_service.delete(item["_id"])
            except Exception as exc:
                ui.notify(f"Could not delete user: {exc}", type="negative", timeout=8000)
                return
            if self.ui_perm_svc:
                await self.ui_perm_svc.delete_user_permissions(item["_id"])
                await self.ui_perm_svc.delete_user_mcp_servers(item["_id"])
            ui.notify("User deleted", type="info")
            if crud:
                crud[0].refresh()

        async def open_user_modal(item: dict | None = None) -> None:
            is_edit = item is not None
            initial_username  = item.get("username", "") if is_edit else ""
            initial_email     = item.get("email", "")    if is_edit else ""
            initial_firstname = item.get("_firstname", "") if is_edit else ""
            initial_lastname  = item.get("_lastname", "") if is_edit else ""
            initial_role      = item.get("role", _PLATFORM_ROLES[0]) if is_edit else _PLATFORM_ROLES[0]
            user_id           = item.get("_id", "") if is_edit else ""

            if is_edit and self.ui_perm_svc and user_id:
                initial_perms = await self.ui_perm_svc.get_user_permissions(user_id) or []
                initial_mcp = await self.ui_perm_svc.get_user_mcp_servers(user_id)
            else:
                initial_perms = []
                initial_mcp = []

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[540px] p-6 gap-4"):
                    ui.label("Edit user" if is_edit else "New user").classes("text-xl font-bold text-slate-800")

                    with ui.column().classes("w-full gap-3"):
                        username_inp = (
                            ui.input(label="Username", value=initial_username)
                            .classes("w-full")
                            .props("outlined dense")
                        )
                        email_inp = (
                            ui.input(label="Email", value=initial_email)
                            .classes("w-full")
                            .props("outlined dense")
                        )
                        with ui.row().classes("w-full gap-2"):
                            firstname_inp = (
                                ui.input(label="First name", value=initial_firstname)
                                .classes("flex-1")
                                .props("outlined dense")
                            )
                            lastname_inp = (
                                ui.input(label="Last name", value=initial_lastname)
                                .classes("flex-1")
                                .props("outlined dense")
                            )
                        if not is_edit:
                            password_inp = (
                                ui.input(label="Password")
                                .classes("w-full")
                                .props("outlined dense type=password")
                            )
                        else:
                            password_inp = None

                        role_sel = (
                            ui.select(_PLATFORM_ROLES, value=initial_role, label="Role")
                            .classes("w-full")
                            .props("outlined dense")
                        )

                    mcp_checkboxes: dict = {}
                    if self.ui_perm_svc:
                        with ui.column().classes("w-full gap-0 mt-2"):
                            ui.label("Direct UI Permissions (override role defaults)").classes("text-sm font-semibold text-slate-700 mb-1")
                            with ui.element("div").classes("w-full max-h-64 overflow-y-auto border border-slate-200 rounded p-2"):
                                picker = render_permission_picker(initial_perms)

                        with ui.column().classes("w-full gap-0 mt-4"):
                            ui.label("MCP Server Access").classes("text-sm font-semibold text-slate-700 mb-1")
                            ui.label(
                                "Servers checked here are directly accessible to this user "
                                "(in addition to what their role grants)."
                            ).classes("text-xs text-slate-400 mb-2")
                            with ui.row().classes("flex-wrap gap-3 border border-slate-200 rounded p-3 w-full"):
                                for sid in MCP_SERVERS:
                                    cb = (
                                        ui.checkbox(_MCP_LABELS.get(sid, sid), value=sid in initial_mcp)
                                        .props("dense")
                                        .classes("text-sm")
                                    )
                                    mcp_checkboxes[sid] = cb
                    else:
                        picker = None

                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def save(d=dlg):
                            username  = username_inp.value.strip()
                            email     = email_inp.value.strip()
                            firstname = firstname_inp.value.strip()
                            lastname  = lastname_inp.value.strip()
                            password  = password_inp.value if password_inp else ""
                            role      = role_sel.value or "member"

                            if not username:
                                ui.notify("Username is required.", type="warning")
                                return

                            try:
                                if is_edit:
                                    await self.user_service.update(
                                        user_id,
                                        username,
                                        email,
                                        firstname,
                                        lastname,
                                        password,
                                        role,
                                    )
                                    resolved_id = user_id
                                else:
                                    resolved_id = await self.user_service.create(
                                        username, email, firstname, lastname, password, role
                                    )
                            except Exception as exc:
                                ui.notify(f"Could not save user: {exc}", type="negative", timeout=8000)
                                return

                            if self.ui_perm_svc and resolved_id:
                                if picker:
                                    selected = picker["get_selected"]()
                                    if selected:
                                        await self.ui_perm_svc.set_user_permissions(resolved_id, selected)
                                    elif is_edit:
                                        await self.ui_perm_svc.delete_user_permissions(resolved_id)

                                selected_mcp = [
                                    sid for sid, cb in mcp_checkboxes.items() if cb.value
                                ]
                                await self.ui_perm_svc.set_user_mcp_servers(resolved_id, selected_mcp)

                            ui.notify("User saved.", type="positive")
                            d.close()
                            if crud:
                                crud[0].refresh()

                        ui.button("Save", on_click=save).props("unelevated color=primary")

            dlg.open()

        async def _open_new_user():
            await open_user_modal()

        tpl = CRUDTemplate(
            title="Users",
            columns=["Username", "Email", "Role"],
            on_refresh=refresh_data,
            on_new_click=_open_new_user,
            on_edit=open_user_modal,
            on_delete=on_delete,
            direct_edit=True,
        )
        crud.append(tpl)
