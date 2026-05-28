"""UI tests for ProfileView.

Verifies identity card, role badge, MCP access badges, and preferences
(model select vs text input) render correctly.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {
    "id": "uid-1",
    "username": "alice",
    "email": "alice@example.com",
    "name": "Alice Wonderland",
}


def _make_page(role: str = "member", mcp_access: bool = True, models: list[str] | None = None):
    from pages.profile import ProfileView

    page = ProfileView.__new__(ProfileView)
    page.fga = MagicMock()
    page.fga.get_object_tuples = AsyncMock(return_value={f"user:{_MOCK_USER['id']}": role})
    page.fga.check = AsyncMock(return_value=mcp_access)
    page.models = MagicMock()
    page.models.installed_chat_models = AsyncMock(
        return_value=models if models is not None else ["ollama/llama3:latest"]
    )
    page.storage = MagicMock()
    page.storage.list_chats = MagicMock(return_value=[])
    page.storage.clear_chat = MagicMock()
    page.storage.delete_chat = MagicMock()
    return page


# ── Identity card ──────────────────────────────────────────────────────────────

async def test_identity_card_shows_user_name() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("Alice Wonderland")


async def test_identity_card_shows_email() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("alice@example.com")


async def test_identity_card_shows_user_id() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("uid-1")


# ── Role badge ─────────────────────────────────────────────────────────────────

async def test_role_badge_shows_admin() -> None:
    async with user_simulation() as user:
        page = _make_page(role="admin")

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("admin")


async def test_role_badge_shows_member() -> None:
    async with user_simulation() as user:
        page = _make_page(role="member")

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("member")


# ── MCP access card ────────────────────────────────────────────────────────────

async def test_mcp_card_shows_server_names() -> None:
    """MCP section shows badge for each known MCP server."""
    async with user_simulation() as user:
        page = _make_page(mcp_access=True)

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("git")
        await user.should_see("jira")


async def test_mcp_card_section_header_visible() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("MCP Access:")


# ── Preferences card ───────────────────────────────────────────────────────────

async def test_preferences_shows_model_select_when_models_available() -> None:
    async with user_simulation() as user:
        page = _make_page(models=["ollama/llama3:latest", "gpt-4o"])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.select).elements)
        assert els, "model select should be present when models are available"


async def test_preferences_shows_text_input_when_no_models() -> None:
    async with user_simulation() as user:
        page = _make_page(models=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        with user:
            inputs = list(user.find(kind=ui.input).elements)
        assert inputs, "text input should be present when no models are available"


async def test_preferences_save_button_visible() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("Save")
