"""UI tests for RolesPermissions page.

Verifies that the roles management tab renders correctly and that the new/edit
modal shows both UI Permissions and MCP Server Access sections.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_PRINCIPALS = {"user:uid-admin": "admin"}

_MOCK_ROLES = [
    {"name": "admin",  "description": "Full platform administration.", "type": "shokan"},
    {"name": "member", "description": "Standard access.",              "type": "shokan"},
]


def _make_page():
    from pages.roles import RolesPermissions
    from services.permissions import UIPermService

    fga = MagicMock()
    fga.get_object_tuples = AsyncMock(return_value={})

    ui_perm_svc = MagicMock(spec=UIPermService)
    ui_perm_svc.get_role_permissions = AsyncMock(return_value=None)
    ui_perm_svc.get_role_mcp_servers = AsyncMock(return_value=[])
    ui_perm_svc.set_role_permissions = AsyncMock()
    ui_perm_svc.set_role_mcp_servers = AsyncMock()
    ui_perm_svc.delete_role_permissions = AsyncMock()
    ui_perm_svc.delete_role_mcp_servers = AsyncMock()

    page = RolesPermissions.__new__(RolesPermissions)
    page.fga = fga
    page._ui_perm_svc = ui_perm_svc

    k8s = MagicMock()
    k8s.read_json.return_value = {"roles": _MOCK_ROLES}
    k8s.write_json.return_value = None
    page.k8s = k8s

    return page, fga, ui_perm_svc


# ── Management tab rendering ───────────────────────────────────────────────────

async def test_roles_management_tab_shows_title() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        await user.should_see("Role definitions")


async def test_roles_management_tab_shows_existing_roles() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        await user.should_see("admin")
        await user.should_see("member")


async def test_roles_management_tab_shows_new_button() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        await user.should_see("New")


async def test_roles_shows_assignments_tab() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        await user.should_see("Assignments")


# ── New role modal content ─────────────────────────────────────────────────────

async def test_new_role_modal_shows_ui_permissions_section() -> None:
    """New role modal contains the UI Permissions section."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        await user.should_see("Management")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("UI Permissions")


async def test_new_role_modal_shows_mcp_section() -> None:
    """New role modal contains the MCP Server Access section."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("MCP Server Access")


async def test_new_role_modal_shows_mcp_checkboxes() -> None:
    """New role modal shows individual MCP server checkboxes."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Git")
        await user.should_see("Jira")
        await user.should_see("Slack")


async def test_new_role_modal_shows_role_name_field() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_PRINCIPALS)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Role name")
