"""UI tests for navigation — verify that menu buttons appear/disappear based on permissions.

Uses the same permission logic (can()) that main.py uses, without importing main.py
(which instantiates cluster singletons). Each test renders a minimal nav inline.
"""

import sys
from pathlib import Path

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


def _render_nav(perms: set[str]) -> None:
    """Mirror of main.py _render_nav — conditional buttons based on permissions."""
    from services.permissions import can

    ui.button("Chat").mark("nav-chat")
    ui.button("Profile").mark("nav-profile")
    if can(perms, "models:menu:read"):
        ui.button("Models").mark("nav-models")
    if can(perms, "datalake:menu:read"):
        ui.button("Data Lake").mark("nav-datalake")
    if can(perms, "settings:menu:read"):
        ui.button("Permissions").mark("nav-settings")
    if can(perms, "system:menu:read"):
        ui.button("System").mark("nav-system")


_ADMIN_PERMS = {
    "models:menu:read",
    "datalake:menu:read",
    "settings:menu:read",
    "system:menu:read",
}
_NO_PERMS: set[str] = set()


# ── Items always visible ───────────────────────────────────────────────────────

async def test_chat_and_profile_always_visible() -> None:
    """Chat and Profile buttons are visible regardless of permissions."""
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_NO_PERMS)

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-chat")
        await user.should_see(kind=ui.button, marker="nav-profile")


# ── Models nav ─────────────────────────────────────────────────────────────────

async def test_models_button_visible_with_models_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav({"models:menu:read"})

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-models")


async def test_models_button_hidden_without_models_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_NO_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="nav-models")


# ── Data Lake nav ──────────────────────────────────────────────────────────────

async def test_datalake_button_visible_with_datalake_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav({"datalake:menu:read"})

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-datalake")


async def test_datalake_button_hidden_without_datalake_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_NO_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="nav-datalake")


# ── Permissions nav ────────────────────────────────────────────────────────────

async def test_settings_button_visible_with_settings_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav({"settings:menu:read"})

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-settings")


async def test_settings_button_hidden_without_settings_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_NO_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="nav-settings")


# ── System nav ─────────────────────────────────────────────────────────────────

async def test_system_button_visible_with_system_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav({"system:menu:read"})

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-system")


async def test_system_button_hidden_without_system_menu_read() -> None:
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_NO_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="nav-system")


# ── Admin sees everything ──────────────────────────────────────────────────────

async def test_admin_perms_show_all_nav_items() -> None:
    """A user with all menu permissions sees all nav buttons."""
    async with user_simulation() as user:
        @ui.page("/test")
        def _():
            _render_nav(_ADMIN_PERMS)

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="nav-chat")
        await user.should_see(kind=ui.button, marker="nav-profile")
        await user.should_see(kind=ui.button, marker="nav-models")
        await user.should_see(kind=ui.button, marker="nav-datalake")
        await user.should_see(kind=ui.button, marker="nav-settings")
        await user.should_see(kind=ui.button, marker="nav-system")
