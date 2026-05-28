"""UI tests for ChatPage.

Verifies that key elements render with the correct state (enabled/disabled,
visible/hidden) depending on model availability and user permissions.

All tests run headless via NiceGUI's user_simulation — no real server needed.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-1", "username": "alice", "email": "alice@example.com", "name": "Alice"}
_ALL_PERMS = {
    "chat:model:update",
    "models:ollama:start",
    "models:menu:read",
    "datalake:menu:read",
    "settings:menu:read",
    "system:menu:read",
}
_MEMBER_PERMS = {"chat:model:update", "models:ollama:start"}


def _make_page(
    available: list[str] | None = None,
    installed: list[str] | None = None,
    fga_check: bool = True,
    chat_history: list[dict] | None = None,
):
    """Return a ChatPage with all I/O dependencies mocked."""
    from pages.chat import ChatPage

    page = ChatPage.__new__(ChatPage)
    page.models = MagicMock()
    page.models.available = AsyncMock(
        return_value=available if available is not None else ["ollama/llama3:latest"]
    )
    page.models.installed_chat_models = AsyncMock(
        return_value=installed if installed is not None else ["ollama/llama3:latest"]
    )
    page.litellm = MagicMock()
    page.litellm.url = "http://litellm:8000"
    page.litellm._headers = {}
    page.ollama = MagicMock()
    page.ollama.url = "http://ollama:11434"
    page.fga = MagicMock()
    page.fga.check = AsyncMock(return_value=fga_check)
    page.chat_service = MagicMock()

    _history = chat_history or []
    page.storage = MagicMock()
    page.storage.list_chats = MagicMock(return_value=[])
    page.storage.load_chat = MagicMock(
        return_value={
            "title": "Test chat",
            "messages": _history,
            "ts": 1_700_000_000.0,
            "compact_summary": "",
            "archived_raw": "",
        }
    )
    page.storage.save_chat = MagicMock()
    page.storage.delete_chat = MagicMock()
    page.storage.needs_compaction = MagicMock(return_value=False)
    page.storage.migrate_from_storage = MagicMock()
    return page


# ── Model selector ─────────────────────────────────────────────────────────────

async def test_model_select_label_visible_when_models_available() -> None:
    """Model selector with its 'Model' label renders when models exist."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"], installed=["ollama/llama3:latest"])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_PERMS)

        await user.open("/test")
        await user.should_see("Model")


async def test_model_select_enabled_with_chat_permission() -> None:
    """Model selector is enabled when user has chat:model:update."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = {e for e in user.find(kind=ui.select, marker="model-select").elements}
        assert els, "model-select element not found"
        assert all(e.enabled for e in els), "model-select should be enabled"


async def test_model_select_disabled_without_chat_permission() -> None:
    """Model selector is disabled when user lacks chat:model:update."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"])
        perms_no_model_change: set[str] = set()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, perms_no_model_change)

        await user.open("/test")
        with user:
            els = {e for e in user.find(kind=ui.select, marker="model-select").elements}
        assert els, "model-select element not found"
        assert all(not e.enabled for e in els), "model-select should be disabled"


async def test_model_select_shows_placeholder_when_no_models() -> None:
    """When no models are available the select shows '—'."""
    async with user_simulation() as user:
        page = _make_page(available=[], installed=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.select, marker="model-select").elements)
        assert els
        select = els[0]
        assert "—" in select.options or "—" in select.options.values()


# ── Send button ────────────────────────────────────────────────────────────────

async def test_send_button_enabled_for_cloud_model() -> None:
    """Send button is enabled when a cloud (non-Ollama) model is selected."""
    async with user_simulation() as user:
        page = _make_page(
            available=["gpt-4o"],
            installed=[],
        )

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.button, marker="send-btn").elements)
        assert els, "send-btn not found"
        assert all(e.enabled for e in els), "send button should be enabled for cloud model"


async def test_send_button_disabled_when_ollama_not_running() -> None:
    """Send button is disabled when selected Ollama model is not in RAM."""
    async with user_simulation() as user:
        # installed but NOT in available (not running in RAM)
        page = _make_page(
            available=[],
            installed=["ollama/llama3:latest"],
        )

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.button, marker="send-btn").elements)
        assert els, "send-btn not found"
        assert all(not e.enabled for e in els), "send button should be disabled when model not ready"


async def test_send_button_disabled_when_no_models() -> None:
    """Send button is disabled when no models are available at all."""
    async with user_simulation() as user:
        page = _make_page(available=[], installed=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.button, marker="send-btn").elements)
        assert els, "send-btn not found"
        assert all(not e.enabled for e in els)


# ── Message input ──────────────────────────────────────────────────────────────

async def test_input_enabled_for_ready_model() -> None:
    """Input field is enabled when the selected model is ready (cloud or running Ollama)."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"], installed=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.input, marker="msg-input").elements)
        assert els, "msg-input not found"
        assert all(e.enabled for e in els)


async def test_input_disabled_when_model_not_ready() -> None:
    """Input field is disabled when no ready model is selected."""
    async with user_simulation() as user:
        page = _make_page(available=[], installed=["ollama/llama3:latest"])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        with user:
            els = list(user.find(kind=ui.input, marker="msg-input").elements)
        assert els, "msg-input not found"
        assert all(not e.enabled for e in els)


# ── Load button ────────────────────────────────────────────────────────────────

async def test_load_button_visible_for_unloaded_ollama() -> None:
    """Load button appears when selected Ollama model is installed but not running."""
    async with user_simulation() as user:
        page = _make_page(
            available=[],
            installed=["ollama/llama3:latest"],
        )

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="load-btn")


async def test_load_button_hidden_for_ready_model() -> None:
    """Load button is hidden when the selected model is already running."""
    async with user_simulation() as user:
        page = _make_page(
            available=["ollama/llama3:latest"],
            installed=["ollama/llama3:latest"],
        )

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="load-btn")


async def test_load_button_hidden_without_ollama_start_permission() -> None:
    """Load button is never shown when user lacks models:ollama:start."""
    async with user_simulation() as user:
        page = _make_page(available=[], installed=["ollama/llama3:latest"])
        perms_no_load = {"chat:model:update"}  # no models:ollama:start

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, perms_no_load)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="load-btn")


async def test_load_button_hidden_for_cloud_model() -> None:
    """Load button does not appear for cloud (non-Ollama) models."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"], installed=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _MEMBER_PERMS)

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="load-btn")


# ── History replay ─────────────────────────────────────────────────────────────

async def test_stored_history_renders_on_page_load() -> None:
    """Messages stored in ChatStorage are displayed when the page loads."""
    history = [
        {"role": "user", "content": "Tell me about Shokan"},
        {"role": "assistant", "content": "Shokan is an agentic platform"},
    ]
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"], chat_history=history)

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_PERMS)

        await user.open("/test")
        await user.should_see("Tell me about Shokan")
        await user.should_see("Shokan is an agentic platform")


# ── Search input ───────────────────────────────────────────────────────────────

async def test_search_input_renders_in_sidebar() -> None:
    """A search input is present in the chat sidebar."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_PERMS)

        await user.open("/test")
        await user.should_see("Search chats…")


# ── Context indicator ──────────────────────────────────────────────────────────

async def test_context_indicator_empty_on_fresh_chat() -> None:
    """Context token label is empty when there are no messages in the active chat."""
    async with user_simulation() as user:
        page = _make_page(available=["gpt-4o"], chat_history=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_PERMS)

        await user.open("/test")
        await user.should_not_see("tokens")
