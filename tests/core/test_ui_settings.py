"""UI tests for PermissionsView (settings page).

Verifies that each tab (Users, Groups, Roles, Data Lake, Models) is shown
or hidden based on the caller's permissions, and that the no-perms fallback
renders correctly.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-1", "username": "admin"}

_ALL_SETTINGS_PERMS = {
    "settings:users:read",
    "settings:groups:read",
    "settings:roles:read",
    "settings:datalake:read",
    "settings:models:read",
}


class _StubSubView:
    """Minimal sub-view that renders a single label so should_see works."""
    async def render(self, *args, **kwargs) -> None:
        ui.label("stub-content")


def _make_page():
    from pages.permissions import PermissionsView

    page = PermissionsView.__new__(PermissionsView)
    kc = MagicMock()
    kc.list_users = AsyncMock(return_value=[])
    kc.list_groups = AsyncMock(return_value=[])
    page._kc = kc
    page._fga = MagicMock()
    page._ui_perm_svc = MagicMock()
    page._users    = _StubSubView()
    page._groups   = _StubSubView()
    page._roles    = _StubSubView()
    page._datalake = _StubSubView()
    page._llm      = _StubSubView()
    return page


# ── No permissions ─────────────────────────────────────────────────────────────

async def test_no_perms_shows_fallback_message() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("No settings tabs available.")


# ── Individual tab visibility ──────────────────────────────────────────────────

async def test_users_tab_visible_with_settings_users_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:users:read"})

        await user.open("/test")
        await user.should_see("Users")


async def test_users_tab_hidden_without_settings_users_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_not_see(kind=ui.tab, marker=None)


async def test_groups_tab_visible_with_settings_groups_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:groups:read"})

        await user.open("/test")
        await user.should_see("Groups")


async def test_roles_tab_visible_with_settings_roles_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:roles:read"})

        await user.open("/test")
        await user.should_see("Roles")


async def test_datalake_tab_visible_with_settings_datalake_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:datalake:read"})

        await user.open("/test")
        # The tab label is "Data Lake" in PermissionsView
        await user.should_see("Data Lake")


async def test_models_tab_visible_with_settings_models_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:models:read"})

        await user.open("/test")
        await user.should_see("Models")


# ── Admin sees all tabs ────────────────────────────────────────────────────────

async def test_admin_perms_show_all_settings_tabs() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_SETTINGS_PERMS)

        await user.open("/test")
        await user.should_see("Users")
        await user.should_see("Groups")
        await user.should_see("Roles")
        await user.should_see("Data Lake")
        await user.should_see("Models")


# ── Tab isolation — only correct tab shown ─────────────────────────────────────

async def test_only_users_tab_with_single_perm() -> None:
    """With only settings:users:read, no other tabs appear."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"settings:users:read"})

        await user.open("/test")
        await user.should_see("Users")
        await user.should_not_see("Groups")
        await user.should_not_see("Roles")
