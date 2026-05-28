"""UI tests for SystemView.

Verifies CronJobs, Indexed Documents, Dashboard, and Audit Log tabs gate on
permissions correctly, and that action buttons are only shown with update permission.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nicegui import ui
from nicegui.testing import user_simulation

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

_MOCK_USER = {"id": "uid-1", "username": "alice"}

_MOCK_JOBS = [
    {
        "k8s_name": "shokan-core-ingester",
        "schedule": "0 2 * * *",
        "suspended": False,
        "age_secs": 86400,
        "last_ok_secs": 3600,
    }
]

_MOCK_DOCS = [
    {
        "source_type": "s3",
        "file_path": "s3://my-bucket/report.pdf",
        "chunks": 12,
        "datasource_id": "ds-001",
    }
]

_MOCK_AUDIT_EVENTS = [
    {
        "ts": 1_700_000_000.0,
        "user_id": "uid-1",
        "action": "chat",
        "resource": "ollama/llama3:latest",
        "details": {"tools_used": False, "query_len": 42},
    },
    {
        "ts": 1_700_000_060.0,
        "user_id": "uid-2",
        "action": "tool_call",
        "resource": "jira__list_issues",
        "details": {"server": "jira"},
    },
]

_MOCK_STATS = {
    "total": 1,
    "by_day": {"2023-11-14": 1},
    "by_model": {"ollama/llama3:latest": 1},
    "by_user": {"uid-1": 1},
}


def _make_page(jobs=None, docs=None, audit_events=None, audit_stats=None):
    from pages.system import SystemView

    page = SystemView.__new__(SystemView)
    page.k8s = MagicMock()
    page.k8s.list_cronjobs = MagicMock(return_value=jobs if jobs is not None else _MOCK_JOBS)
    page.qdrant = MagicMock()
    page.kc = MagicMock()
    page.kc.list_users = AsyncMock(return_value=[])
    page.storage = MagicMock()
    page.storage.list_all_users = MagicMock(return_value=[])
    page.audit = MagicMock()
    page.audit.recent = MagicMock(
        return_value=audit_events if audit_events is not None else _MOCK_AUDIT_EVENTS
    )
    page.audit.stats = MagicMock(
        return_value=audit_stats if audit_stats is not None else _MOCK_STATS
    )
    return page


# ── CronJobs tab ───────────────────────────────────────────────────────────────

async def test_cronjobs_tab_shows_content_with_permission() -> None:
    """CronJobs tab renders the RAG job when user has system:cronjobs:read."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_see("RAG")


async def test_cronjobs_tab_shows_access_denied_without_permission() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        await user.should_see("Access denied.")


async def test_cronjobs_tab_shows_schedule() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_see("0 2 * * *")


async def test_cronjobs_action_buttons_visible_with_update_perm() -> None:
    """Toggle, run-now, and edit buttons appear when user has system:cronjobs:update."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read", "system:cronjobs:update"})

        await user.open("/test")
        await user.should_see(kind=ui.button, marker="cj-toggle-btn")
        await user.should_see(kind=ui.button, marker="cj-run-btn")
        await user.should_see(kind=ui.button, marker="cj-edit-btn")


async def test_cronjobs_action_buttons_hidden_without_update_perm() -> None:
    """No action buttons when user has read but not update."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_not_see(kind=ui.button, marker="cj-toggle-btn")
        await user.should_not_see(kind=ui.button, marker="cj-run-btn")
        await user.should_not_see(kind=ui.button, marker="cj-edit-btn")


async def test_cronjobs_shows_no_jobs_message_when_empty() -> None:
    async with user_simulation() as user:
        page = _make_page(jobs=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_see("No CronJobs found.")


# ── Indexed Documents tab ──────────────────────────────────────────────────────

async def test_rag_tab_shows_content_with_permission() -> None:
    """Indexed Documents tab renders when user has system:rag_index:read."""
    with patch("pages.system.scroll_qdrant_index", return_value=_MOCK_DOCS):
        async with user_simulation() as user:
            page = _make_page()

            @ui.page("/test")
            async def _():
                await page.render(_MOCK_USER, {"system:rag_index:read"})

            await user.open("/test")
            await user.should_see("Indexed Documents")


async def test_rag_tab_shows_access_denied_without_permission() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, set())

        await user.open("/test")
        with user:
            labels = [e.text for e in user.find(kind=ui.label).elements if "Access denied." in (e.text or "")]
        assert labels, "Expected 'Access denied.' label for rag_index without permission"


async def test_rag_tab_shows_file_path() -> None:
    with patch("pages.system.scroll_qdrant_index", return_value=_MOCK_DOCS):
        async with user_simulation() as user:
            page = _make_page()

            @ui.page("/test")
            async def _():
                await page.render(_MOCK_USER, {"system:rag_index:read"})

            await user.open("/test")
            # s3 path strips bucket — shows "report.pdf"
            await user.should_see("report.pdf")


async def test_rag_tab_shows_summary_counts() -> None:
    with patch("pages.system.scroll_qdrant_index", return_value=_MOCK_DOCS):
        async with user_simulation() as user:
            page = _make_page()

            @ui.page("/test")
            async def _():
                await page.render(_MOCK_USER, {"system:rag_index:read"})

            await user.open("/test")
            await user.should_see("1 files")


async def test_rag_tab_empty_when_no_docs() -> None:
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            with patch("pages.system.scroll_qdrant_index", return_value=[]):
                await page.render(_MOCK_USER, {"system:rag_index:read"})

        await user.open("/test")
        await user.should_see("No documents indexed yet.")


# ── Dashboard tab ──────────────────────────────────────────────────────────────

async def test_dashboard_tab_visible_with_permission() -> None:
    """Dashboard tab renders when user has system:dashboard:read."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:dashboard:read"})

        await user.open("/test")
        await user.should_see("Dashboard")


async def test_dashboard_tab_hidden_without_permission() -> None:
    """Dashboard tab is not rendered without system:dashboard:read."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_not_see("Dashboard")


async def test_dashboard_shows_total_queries() -> None:
    """Dashboard renders the total query count from audit stats."""
    async with user_simulation() as user:
        page = _make_page(audit_stats=_MOCK_STATS)

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:dashboard:read"})

        await user.open("/test")
        await user.should_see("Total Queries")
        await user.should_see("1")


async def test_dashboard_shows_no_data_message_when_empty() -> None:
    """Dashboard shows placeholder when no audit data exists."""
    empty_stats = {"total": 0, "by_day": {}, "by_model": {}, "by_user": {}}
    async with user_simulation() as user:
        page = _make_page(audit_stats=empty_stats)

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:dashboard:read"})

        await user.open("/test")
        await user.should_see("No query data yet")


# ── Audit Log tab ──────────────────────────────────────────────────────────────

async def test_audit_tab_visible_with_permission() -> None:
    """Audit Log tab renders when user has system:audit:read."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:audit:read"})

        await user.open("/test")
        await user.should_see("Audit Log")


async def test_audit_tab_hidden_without_permission() -> None:
    """Audit Log tab is not rendered without system:audit:read."""
    async with user_simulation() as user:
        page = _make_page()

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:cronjobs:read"})

        await user.open("/test")
        await user.should_not_see("Audit Log")


async def test_audit_log_shows_events() -> None:
    """Audit Log tab renders action and resource from recent events."""
    async with user_simulation() as user:
        page = _make_page(audit_events=_MOCK_AUDIT_EVENTS)

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:audit:read"})

        await user.open("/test")
        await user.should_see("chat")
        await user.should_see("tool_call")


async def test_audit_log_shows_no_events_message_when_empty() -> None:
    """Audit Log shows empty placeholder when there are no events."""
    async with user_simulation() as user:
        page = _make_page(audit_events=[])

        @ui.page("/test")
        async def _():
            await page.render(_MOCK_USER, {"system:audit:read"})

        await user.open("/test")
        await user.should_see("No audit events found.")
