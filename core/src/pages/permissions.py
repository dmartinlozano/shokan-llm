"""
Shokan-LLM permissions settings — page class.

Imported by main.py and rendered at /settings.
No entrypoint, no auth routes, no standalone startup.
Requires a NiceGUI @ui.page context to call render().
"""

import asyncio

from nicegui import ui

from connectors.k8s import K8s
from connectors.keycloak import Keycloak
from connectors.litellm import LiteLLM
from connectors.openfga import OpenFGA
from connectors.rag import RAG
from services.models import LLMModels
from services.permissions import UIPermService, can
from services.users import UserService
from services.groups import GroupService

from pages.datalake import DataLakePermissions
from pages.groups import GroupsPermissions
from pages.models import LlmModelsPermissions
from pages.rag import RagPermissions
from pages.roles import RolesPermissions
from pages.users import UsersPermissions


class PermissionsView:
    """Renders the admin permissions settings page.

    Tabs: Users · Groups · Roles · Data Lake · Models.

    Instantiate once per application startup; call render(user, perms) per page visit.
    """

    def __init__(self) -> None:
        fga = OpenFGA()
        kc = Keycloak()
        k8s = K8s()
        litellm = LiteLLM()
        rag = RAG(k8s, fga)
        llm_models = LLMModels(litellm, fga)
        user_service = UserService(kc, fga)
        group_service = GroupService(kc, fga)
        ui_perm_svc = UIPermService(fga)

        self._kc = kc
        self._fga = fga
        self._ui_perm_svc = ui_perm_svc
        self._users  = UsersPermissions(fga, user_service, ui_perm_svc)
        self._groups = GroupsPermissions(fga, group_service)
        self._roles  = RolesPermissions(fga, rag, llm_models)
        self._datalake = DataLakePermissions(fga, rag)
        self._llm = LlmModelsPermissions(fga, llm_models)

    async def render(self, user: dict, perms: set[str] | None = None) -> None:
        """Build the permissions settings UI. Must be called within a NiceGUI page context."""
        if perms is None:
            perms = set()

        principals = await self._load_principals()

        show_users    = can(perms, "settings:users:read")
        show_groups   = can(perms, "settings:groups:read")
        show_roles    = can(perms, "settings:roles:read")
        show_datalake = can(perms, "settings:datalake:read")
        show_models   = can(perms, "settings:models:read")

        with ui.tabs().classes("w-full bg-gray-100") as tabs:
            tab_users    = ui.tab("Users",     icon="person")         if show_users    else None
            tab_groups   = ui.tab("Groups",    icon="group")          if show_groups   else None
            tab_roles    = ui.tab("Roles",     icon="policy")         if show_roles    else None
            tab_datalake = ui.tab("Data Lake", icon="storage")        if show_datalake else None
            tab_llm      = ui.tab("Models",    icon="model_training") if show_models   else None

        first_tab = next(
            (t for t in [tab_users, tab_groups, tab_roles, tab_datalake, tab_llm] if t is not None),
            None,
        )
        if first_tab is None:
            ui.label("No settings tabs available.").classes("text-gray-500 text-sm p-4")
            return

        with ui.tab_panels(tabs, value=first_tab).classes("w-full"):
            if show_users and tab_users:
                with ui.tab_panel(tab_users):
                    await self._users.render(principals, user)
            if show_groups and tab_groups:
                with ui.tab_panel(tab_groups):
                    await self._groups.render(principals, user)
            if show_roles and tab_roles:
                with ui.tab_panel(tab_roles):
                    await self._roles.render(principals)
            if show_datalake and tab_datalake:
                with ui.tab_panel(tab_datalake):
                    await self._datalake.render(principals)
            if show_models and tab_llm:
                with ui.tab_panel(tab_llm):
                    await self._llm.render(principals)

    async def _load_principals(self) -> dict[str, str]:
        """Return {fga_subject: display_label} built from Keycloak users and groups."""
        try:
            users, groups = await asyncio.gather(
                self._kc.list_users(),
                self._kc.list_groups(),
            )
        except Exception:
            return {}
        result: dict[str, str] = {}
        for u in users:
            result[f"user:{u['id']}"] = f"user:{u.get('username', u['id'])}"
        for g in groups:
            result[f"group:{g['id']}#member"] = f"group:{g.get('name', g['id'])}"
        return result
