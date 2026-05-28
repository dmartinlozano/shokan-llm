"""UI tests for UsersPermissions page.

Verifies that the users table renders correctly and that the new/edit modal
contains both the Direct UI Permissions and MCP Server Access sections.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-admin", "username": "admin"}

_MOCK_USERS = [
    {
        "_id": "u1",
        "username": "alice",
        "email": "alice@example.com",
        "role": "member",
        "_firstname": "Alice",
        "_lastname": "Smith",
    }
]


def _make_page(user_list=None):
    from pages.users import UsersPermissions
    from services.permissions import UIPermService

    user_service = MagicMock()
    user_service.list_with_roles = AsyncMock(return_value=_MOCK_USERS if user_list is None else user_list)
    user_service.delete = AsyncMock()

    ui_perm_svc = MagicMock(spec=UIPermService)
    ui_perm_svc.get_user_permissions = AsyncMock(return_value=[])
    ui_perm_svc.get_user_mcp_servers = AsyncMock(return_value=[])
    ui_perm_svc.set_user_permissions = AsyncMock()
    ui_perm_svc.set_user_mcp_servers = AsyncMock()
    ui_perm_svc.delete_user_permissions = AsyncMock()
    ui_perm_svc.delete_user_mcp_servers = AsyncMock()

    fga = MagicMock()
    return UsersPermissions(fga, user_service, ui_perm_svc), user_service, ui_perm_svc


# ── Table rendering ────────────────────────────────────────────────────────────

async def test_users_table_shows_title() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        await user.should_see("Users")


async def test_users_table_shows_user_row() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        await user.should_see("alice")


async def test_users_table_shows_new_button() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        await user.should_see("New")


async def test_users_table_empty_state() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page(user_list=[])

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        await asyncio.sleep(0.05)
        await user.should_see("No items.", retries=10)


# ── New user modal content ─────────────────────────────────────────────────────

async def test_new_user_modal_shows_ui_permissions_section() -> None:
    """New user modal contains the Direct UI Permissions section."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Direct UI Permissions")


async def test_new_user_modal_shows_mcp_section() -> None:
    """New user modal contains the MCP Server Access section."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("MCP Server Access")


async def test_new_user_modal_shows_mcp_checkboxes() -> None:
    """New user modal shows individual MCP server checkboxes."""
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Git")
        await user.should_see("Jira")
        await user.should_see("Slack")


async def test_new_user_modal_shows_username_field() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Username")


async def test_new_user_modal_shows_role_selector() -> None:
    async with user_simulation() as user:
        page, _, _ = _make_page()

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_see("Role")


# ── Without ui_perm_svc ────────────────────────────────────────────────────────

async def test_new_user_modal_without_perm_svc_has_no_permissions_section() -> None:
    """When ui_perm_svc is None, the permissions sections are omitted."""
    async with user_simulation() as user:
        from pages.users import UsersPermissions

        user_service = MagicMock()
        user_service.list_with_roles = AsyncMock(return_value=_MOCK_USERS)
        page = UsersPermissions(MagicMock(), user_service, ui_perm_svc=None)

        @ui.page("/test")
        async def _():
            await page.render({}, _MOCK_USER)

        await user.open("/test")
        user.find(kind=ui.button, content="New").click()
        await asyncio.sleep(0.05)
        await user.should_not_see("Direct UI Permissions")
        await user.should_not_see("MCP Server Access")
