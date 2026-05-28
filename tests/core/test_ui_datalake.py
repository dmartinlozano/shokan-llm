"""UI tests for DatalakeView.

Verifies that each of the 10 data source tabs (S3, Google Drive, Filesystem,
SFTP, Git, Jira, Confluence, Slack, Gmail, Discord) is shown or hidden
based on the caller's datalake:{source}:read permission.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-1", "username": "alice"}

_ALL_DATALAKE_PERMS = {
    "datalake:s3:read",
    "datalake:gdrive:read",
    "datalake:filesystem:read",
    "datalake:sftp:read",
    "datalake:git:read",
    "datalake:jira:read",
    "datalake:confluence:read",
    "datalake:slack:read",
    "datalake:gmail:read",
    "datalake:discord:read",
}


def _make_page():
    from pages.datalake import DatalakeView

    page = DatalakeView.__new__(DatalakeView)

    # Mock rag_settings (RagView)
    rag_cfg = MagicMock()
    rag_cfg.list_s3_buckets.return_value = []
    rag_cfg.list_s3_credentials.return_value = []
    rag_cfg.list_gdrive_folders.return_value = []
    rag_cfg.list_gdrive_credentials.return_value = []
    rag_cfg.list_volumes.return_value = []
    rag_cfg.list_sftp_connections.return_value = []
    rag_cfg.list_sftp_credentials.return_value = []

    rag_settings = MagicMock()
    rag_settings.rag_cfg = rag_cfg
    rag_settings._render_s3_tab = AsyncMock()
    rag_settings._render_gdrive_tab = AsyncMock()
    rag_settings._render_filesystem_tab = AsyncMock()
    rag_settings._render_sftp_tab = AsyncMock()

    # Mock mcp_settings (McpView)
    mcp = MagicMock()
    mcp.get_git_config.return_value = {}
    mcp.list_instances.return_value = []

    mcp_settings = MagicMock()
    mcp_settings.mcp = mcp
    mcp_settings._server_tab = AsyncMock()

    page.rag_settings = rag_settings
    page.mcp_settings = mcp_settings
    return page


# ── No permissions ─────────────────────────────────────────────────────────────

async def test_no_perms_shows_fallback_message() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("No Data Lake sources available.")


# ── RAG cold sources ───────────────────────────────────────────────────────────

async def test_s3_tab_visible_with_datalake_s3_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:s3:read"})

        await user.open("/test")
        await user.should_see("Amazon S3")


async def test_s3_tab_hidden_without_datalake_s3_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_not_see("Amazon S3")


async def test_gdrive_tab_visible_with_datalake_gdrive_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:gdrive:read"})

        await user.open("/test")
        await user.should_see("Google Drive")


async def test_gdrive_tab_hidden_without_datalake_gdrive_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:s3:read"})

        await user.open("/test")
        await user.should_not_see("Google Drive")


async def test_filesystem_tab_visible_with_datalake_filesystem_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:filesystem:read"})

        await user.open("/test")
        await user.should_see("Filesystem")


async def test_sftp_tab_visible_with_datalake_sftp_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:sftp:read"})

        await user.open("/test")
        await user.should_see("SFTP")


# ── MCP live connectors ────────────────────────────────────────────────────────

async def test_git_tab_visible_with_datalake_git_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:git:read"})

        await user.open("/test")
        await user.should_see("Git")


async def test_jira_tab_visible_with_datalake_jira_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:jira:read"})

        await user.open("/test")
        await user.should_see("Jira")


async def test_confluence_tab_visible_with_datalake_confluence_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:confluence:read"})

        await user.open("/test")
        await user.should_see("Confluence")


async def test_slack_tab_visible_with_datalake_slack_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:slack:read"})

        await user.open("/test")
        await user.should_see("Slack")


async def test_gmail_tab_visible_with_datalake_gmail_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:gmail:read"})

        await user.open("/test")
        await user.should_see("Gmail")


async def test_discord_tab_visible_with_datalake_discord_read() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"datalake:discord:read"})

        await user.open("/test")
        await user.should_see("Discord")


# ── Admin sees all tabs ────────────────────────────────────────────────────────

async def test_all_perms_show_all_datalake_tabs() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, _ALL_DATALAKE_PERMS)

        await user.open("/test")
        await user.should_see("Amazon S3")
        await user.should_see("Google Drive")
        await user.should_see("Filesystem")
        await user.should_see("SFTP")
        await user.should_see("Git")
        await user.should_see("Jira")
        await user.should_see("Confluence")
        await user.should_see("Slack")
        await user.should_see("Gmail")
        await user.should_see("Discord")
