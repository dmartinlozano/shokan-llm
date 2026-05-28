"""UI tests for LitellmView (models settings page).

Verifies that Cloud Models, Ollama, Routing, A2A / Agents, and Usage tabs are shown
or hidden based on the caller's models:{tab}:read permission. Usage tab is always
visible regardless of permissions.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-1", "username": "alice"}

_ALL_MODELS_PERMS = {
    "models:cloud:read",
    "models:ollama:read",
    "models:routing:read",
    "models:a2a:read",
    "models:agents:read",
    "models:skills:read",
}


def _make_page():
    from pages.models import LitellmView

    page = LitellmView.__new__(LitellmView)
    page._render_cloud_models_tab = AsyncMock()
    page._render_router_tab = AsyncMock()
    page._render_a2a_tab = AsyncMock()
    page._render_agents_tab = AsyncMock()
    page._render_skills_tab = MagicMock()
    page._render_usage_tab = AsyncMock()
    page.ollama_settings = MagicMock()
    page.ollama_settings.render = AsyncMock()
    page.audit = MagicMock()
    return page


# ── No permissions — Usage tab always shown ────────────────────────────────────

async def test_no_perms_shows_usage_tab() -> None:
    """Usage tab is always visible even without any explicit permissions."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("Usage")


async def test_no_perms_hides_cloud_ollama_routing_a2a() -> None:
    """Without permissions, Cloud/Ollama/Routing/A2A tabs are all hidden."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_not_see("Cloud Models")
        await user.should_not_see("Ollama")
        await user.should_not_see("Routing")
        await user.should_not_see("A2A / Agents")


# ── Individual tab visibility ──────────────────────────────────────────────────

async def test_cloud_models_tab_visible_with_models_cloud_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_see("Cloud Models")


async def test_cloud_models_tab_hidden_without_models_cloud_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:ollama:read"})

        await user.open("/test")
        await user.should_not_see("Cloud Models")


async def test_ollama_tab_visible_with_models_ollama_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:ollama:read"})

        await user.open("/test")
        await user.should_see("Ollama")


async def test_ollama_tab_hidden_without_models_ollama_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_not_see("Ollama")


async def test_routing_tab_visible_with_models_routing_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:routing:read"})

        await user.open("/test")
        await user.should_see("Routing")


async def test_routing_tab_hidden_without_models_routing_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_not_see("Routing")


async def test_a2a_tab_visible_with_models_a2a_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:a2a:read"})

        await user.open("/test")
        await user.should_see("A2A / Agents")


async def test_a2a_tab_hidden_without_models_a2a_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_not_see("A2A / Agents")


# ── Usage tab ──────────────────────────────────────────────────────────────────

async def test_usage_tab_always_visible_with_all_perms() -> None:
    """Usage tab appears alongside all other tabs when admin has all permissions."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_MODELS_PERMS)

        await user.open("/test")
        await user.should_see("Usage")


async def test_usage_tab_visible_with_single_cloud_perm() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_see("Usage")


# ── Admin sees all tabs ────────────────────────────────────────────────────────

async def test_all_perms_show_all_model_tabs() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_MODELS_PERMS)

        await user.open("/test")
        await user.should_see("Cloud Models")
        await user.should_see("Ollama")
        await user.should_see("Routing")
        await user.should_see("A2A / Agents")
        await user.should_see("Usage")


# ── Tab isolation ──────────────────────────────────────────────────────────────

async def test_only_cloud_tab_with_single_perm() -> None:
    """With only models:cloud:read, no other gated tabs appear (Usage always shown)."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"models:cloud:read"})

        await user.open("/test")
        await user.should_see("Cloud Models")
        await user.should_not_see("Routing")
        await user.should_not_see("A2A / Agents")
