"""Roles — management (CRUD definitions) + assignments table."""

import asyncio

from nicegui import ui

from templates.crud_template import CRUDTemplate
from templates.permission_picker import render_permission_picker
from connectors.k8s import K8s
from connectors.mcp import SERVERS as MCP_SERVERS
from connectors.openfga import SHOKAN_OBJECT, OpenFGA
from services.permissions import UIPermService

_SHOKAN_ROLES = ["admin", "member"]

_MCP_LABELS = {
    "git": "Git", "jira": "Jira", "confluence": "Confluence",
    "slack": "Slack", "gmail": "Gmail", "discord": "Discord",
}

_DEFAULT_ROLES: list[dict] = [
    {"name": "admin",  "description": "Full platform administration: manage users, configure models, MCP connectors and data sources."},
    {"name": "member", "description": "Standard access: can use the chat and any resources explicitly granted."},
]

_K8S_KEY = "role-definitions"


class RolesPermissions:
    def __init__(self, fga: OpenFGA, rag, llm_models) -> None:
        self.fga = fga
        self.k8s = K8s()
        self._ui_perm_svc = UIPermService(fga)

    def _load_roles(self) -> list[dict]:
        raw = self.k8s.read_json(_K8S_KEY).get("roles")
        if not raw:
            self.k8s.write_json(_K8S_KEY, {"roles": _DEFAULT_ROLES})
            return list(_DEFAULT_ROLES)
        roles = [{"name": r["name"], "description": r.get("description", ""), "type": r.get("type", "shokan"), "permissions": r.get("permissions")} for r in raw if r.get("type", "shokan") == "shokan"]
        if len(roles) != len(raw):
            self.k8s.write_json(_K8S_KEY, {"roles": roles})
        return roles

    def _save_roles(self, roles: list[dict]) -> None:
        self.k8s.write_json(_K8S_KEY, {"roles": roles})

    async def render(self, principals: dict[str, str]) -> None:
        with ui.tabs().classes("w-full bg-gray-50") as tabs:
            tab_mgmt   = ui.tab("Management",  icon="manage_accounts")
            tab_assign = ui.tab("Assignments", icon="assignment_ind")

        with ui.tab_panels(tabs, value=tab_mgmt).classes("w-full"):
            with ui.tab_panel(tab_mgmt):
                self._render_management_tab()
            with ui.tab_panel(tab_assign):
                _render_assignments_table(self.fga, principals)

    def _render_management_tab(self) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        crud: list[CRUDTemplate] = []

        async def refresh_data():
            roles = await asyncio.to_thread(self._load_roles)
            return [
                {
                    "name":        r["name"],
                    "description": r.get("description", ""),
                    "_idx":        i,
                }
                for i, r in enumerate(roles)
            ]

        async def on_delete(item: dict):
            roles = await asyncio.to_thread(self._load_roles)
            idx = item["_idx"]
            if 0 <= idx < len(roles):
                roles.pop(idx)
                await asyncio.to_thread(self._save_roles, roles)
            role_name = item.get("name", "")
            if role_name:
                await self._ui_perm_svc.delete_role_permissions(role_name)
                await self._ui_perm_svc.delete_role_mcp_servers(role_name)
            ui.notify("Role removed.", type="info")
            if crud:
                crud[0].refresh()

        async def open_role_modal(item: dict | None = None) -> None:
            is_edit = item is not None
            initial_name = item["name"] if is_edit else ""
            initial_desc = item.get("description", "") if is_edit else ""
            initial_perms = await self._ui_perm_svc.get_role_permissions(initial_name) or [] if is_edit else []
            initial_mcp = await self._ui_perm_svc.get_role_mcp_servers(initial_name) if is_edit else []

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[540px] p-6 gap-4"):
                    ui.label("Edit role" if is_edit else "New role definition").classes("text-xl font-bold text-slate-800")

                    with ui.column().classes("w-full gap-3"):
                        name_inp = (
                            ui.input(label="Role name", placeholder="admin", value=initial_name)
                            .classes("w-full")
                            .props("outlined dense")
                        )
                        desc_inp = (
                            ui.textarea(label="Description", value=initial_desc)
                            .classes("w-full")
                            .props("outlined dense rows=3")
                        )

                    picker_container = ui.column().classes("w-full gap-0")
                    picker_ref: dict = {}

                    def _rebuild_picker():
                        picker_container.clear()
                        with picker_container:
                            ui.label("UI Permissions").classes("text-sm font-semibold text-slate-700 mt-2 mb-1")
                            with ui.element("div").classes("w-full max-h-64 overflow-y-auto border border-slate-200 rounded p-2"):
                                picker_ref.update(render_permission_picker(initial_perms if is_edit else []))

                            ui.label("MCP Server Access").classes("text-sm font-semibold text-slate-700 mt-4 mb-1")
                            ui.label(
                                "Servers checked here are accessible to users with this role. "
                                "Admins always have full access regardless of this setting."
                            ).classes("text-xs text-slate-400 mb-2")
                            with ui.row().classes("flex-wrap gap-3 border border-slate-200 rounded p-3 w-full"):
                                for sid in MCP_SERVERS:
                                    checked = sid in initial_mcp
                                    cb = (
                                        ui.checkbox(_MCP_LABELS.get(sid, sid), value=checked)
                                        .props("dense")
                                        .classes("text-sm")
                                    )
                                    picker_ref[f"mcp:{sid}"] = cb

                    _rebuild_picker()

                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def save(d=dlg):
                            role_name = name_inp.value.strip()
                            role_desc = desc_inp.value.strip()

                            if not role_name:
                                ui.notify("Role name is required.", type="warning")
                                return

                            roles = await asyncio.to_thread(self._load_roles)
                            if is_edit:
                                idx = item["_idx"]
                                if 0 <= idx < len(roles):
                                    roles[idx] = {"name": role_name, "description": role_desc, "type": "shokan"}
                            else:
                                roles.append({"name": role_name, "description": role_desc, "type": "shokan"})

                            await asyncio.to_thread(self._save_roles, roles)

                            if picker_ref.get("get_selected"):
                                selected = picker_ref["get_selected"]()
                                await self._ui_perm_svc.set_role_permissions(role_name, selected)

                            selected_mcp = [
                                sid for sid in MCP_SERVERS
                                if picker_ref.get(f"mcp:{sid}") and picker_ref[f"mcp:{sid}"].value
                            ]
                            await self._ui_perm_svc.set_role_mcp_servers(role_name, selected_mcp)

                            ui.notify("Role saved.", type="positive")
                            d.close()
                            if crud:
                                crud[0].refresh()

                        ui.button("Save", on_click=save).props("unelevated color=primary")

            dlg.open()

        async def _open_new_role():
            await open_role_modal()

        tpl = CRUDTemplate(
            title="Role definitions",
            columns=["Name", "Description"],
            on_refresh=refresh_data,
            on_new_click=_open_new_role,
            on_edit=open_role_modal,
            on_delete=on_delete,
            direct_edit=True,
            direct_edit_refresh=False,
        )
        crud.append(tpl)


def _render_assignments_table(fga: OpenFGA, principals: dict[str, str]) -> None:
    from nicegui import context as ng_context
    page_slot = ng_context.client.layout.default_slot
    label_to_key   = {v: k for k, v in principals.items()}
    subject_labels = list(principals.values())
    crud: list[CRUDTemplate] = []

    async def refresh_data():
        tuples = await fga.get_object_tuples(SHOKAN_OBJECT)
        rows = []
        for subj, rel in tuples.items():
            if rel not in _SHOKAN_ROLES:
                continue
            rows.append({
                "subject":      principals.get(subj, subj),
                "role":         rel,
                "_subject_key": subj,
                "_obj":         SHOKAN_OBJECT,
            })
        return rows

    def open_role_modal(item: dict | None = None) -> None:
        is_edit      = item is not None
        initial_subj = item["subject"] if is_edit else (subject_labels[0] if subject_labels else "")
        initial_role = item["role"] if is_edit and item["role"] in _SHOKAN_ROLES else _SHOKAN_ROLES[0]

        with page_slot:
            with ui.dialog() as dlg, ui.card().classes("w-[480px] p-6 gap-4"):
                ui.label("Edit role" if is_edit else "New role assignment").classes("text-xl font-bold text-slate-800")
                with ui.column().classes("w-full gap-3"):
                    subj_sel = ui.select(subject_labels, value=initial_subj, label="User").classes("w-full").props("outlined dense")
                    role_sel = ui.select(_SHOKAN_ROLES, value=initial_role, label="Role").classes("w-full").props("outlined dense")

                with ui.row().classes("justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dlg.close).props("flat")

                    async def save(d=dlg):
                        subj = label_to_key.get(subj_sel.value, subj_sel.value)
                        role = role_sel.value
                        if not subj or not role:
                            ui.notify("All fields are required.", type="warning")
                            return
                        if is_edit:
                            old_subj = item["_subject_key"]
                            old_role = item["role"]
                            old_obj  = item["_obj"]
                            if subj == old_subj and role == old_role:
                                d.close()
                                return
                            await fga.write(
                                writes=[{"user": subj, "relation": role, "object": SHOKAN_OBJECT}],
                                deletes=[{"user": old_subj, "relation": old_role, "object": old_obj}],
                            )
                        else:
                            await fga.set_relation(subj, role, None, SHOKAN_OBJECT)
                        ui.notify("Role saved.", type="positive")
                        d.close()
                        if crud:
                            crud[0].refresh()

                    ui.button("Save", on_click=save).props("unelevated color=primary")
        dlg.open()

    async def on_delete(item: dict):
        await fga.remove_relation(item["_subject_key"], item["role"], item["_obj"])
        ui.notify("Role removed.", type="info")

    tpl = CRUDTemplate(
        title="Role assignments",
        columns=["Subject", "Role"],
        on_refresh=refresh_data,
        on_new_click=lambda: open_role_modal(),
        on_edit=lambda item: open_role_modal(item),
        on_delete=on_delete,
        direct_edit=True,
    )
    crud.append(tpl)
