"""Unit tests for UIPermService in services/permissions.py.

Tests the full set of CRUD methods for role and user permissions and
MCP server access grants, including the new delete_role_* and user MCP methods.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


def _fga_tuples(objects: list[str], subject: str, relation: str):
    return [{"key": {"user": subject, "relation": relation, "object": obj}} for obj in objects]


def _make_svc():
    from services.permissions import UIPermService

    fga = MagicMock()
    fga.write = AsyncMock()
    fga.read_tuples_by_user = AsyncMock(return_value=[])
    return UIPermService(fga), fga


# ── get_role_permissions ────────────────────────────────────────────────────────

async def test_get_role_permissions_returns_default_for_admin_when_no_fga_entries():
    svc, fga = _make_svc()
    result = await svc.get_role_permissions("admin")
    assert result == ["*"]


async def test_get_role_permissions_returns_default_for_member_when_no_fga_entries():
    svc, fga = _make_svc()
    result = await svc.get_role_permissions("member")
    assert "chat:model:update" in result


async def test_get_role_permissions_returns_fga_entries_when_present():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["ui_permission:models:menu:read", "ui_permission:chat:model:update"],
        "shokan:shokanllm#editor",
        "allowed_role",
    )
    result = await svc.get_role_permissions("editor")
    assert set(result) == {"models:menu:read", "chat:model:update"}


# ── delete_role_permissions ─────────────────────────────────────────────────────

async def test_delete_role_permissions_removes_all_fga_ui_permission_tuples():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["ui_permission:models:menu:read", "ui_permission:chat:model:update"],
        "shokan:shokanllm#editor",
        "allowed_role",
    )
    await svc.delete_role_permissions("editor")
    fga.write.assert_called_once()
    kwargs = fga.write.call_args.kwargs
    assert any(d["object"] == "ui_permission:models:menu:read" for d in kwargs["deletes"])
    assert any(d["object"] == "ui_permission:chat:model:update" for d in kwargs["deletes"])


async def test_delete_role_permissions_is_noop_when_no_perms_set():
    svc, fga = _make_svc()
    await svc.delete_role_permissions("new_role")
    fga.write.assert_not_called()


# ── get_role_mcp_servers ────────────────────────────────────────────────────────

async def test_get_role_mcp_servers_returns_server_ids():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "mcp_server:jira", "ui_permission:chat:model:update"],
        "shokan:shokanllm#member",
        "allowed_role",
    )
    result = await svc.get_role_mcp_servers("member")
    assert set(result) == {"git", "jira"}


async def test_get_role_mcp_servers_returns_empty_when_none():
    svc, fga = _make_svc()
    result = await svc.get_role_mcp_servers("member")
    assert result == []


# ── delete_role_mcp_servers ─────────────────────────────────────────────────────

async def test_delete_role_mcp_servers_removes_fga_tuples():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "mcp_server:slack"],
        "shokan:shokanllm#member",
        "allowed_role",
    )
    await svc.delete_role_mcp_servers("member")
    fga.write.assert_called_once()
    kwargs = fga.write.call_args.kwargs
    assert any(d["object"] == "mcp_server:git" for d in kwargs["deletes"])
    assert any(d["object"] == "mcp_server:slack" for d in kwargs["deletes"])


async def test_delete_role_mcp_servers_is_noop_when_none_assigned():
    svc, fga = _make_svc()
    await svc.delete_role_mcp_servers("member")
    fga.write.assert_not_called()


# ── get_user_mcp_servers ────────────────────────────────────────────────────────

async def test_get_user_mcp_servers_returns_server_ids():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "mcp_server:jira"],
        "user:uid1",
        "allowed_user",
    )
    result = await svc.get_user_mcp_servers("uid1")
    assert set(result) == {"git", "jira"}


async def test_get_user_mcp_servers_ignores_non_mcp_tuples():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "ui_permission:chat:model:update"],
        "user:uid1",
        "allowed_user",
    )
    result = await svc.get_user_mcp_servers("uid1")
    assert result == ["git"]


async def test_get_user_mcp_servers_returns_empty_when_none():
    svc, fga = _make_svc()
    result = await svc.get_user_mcp_servers("uid1")
    assert result == []


# ── set_user_mcp_servers ────────────────────────────────────────────────────────

async def test_set_user_mcp_servers_writes_new_and_deletes_old():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "mcp_server:slack"],
        "user:uid1",
        "allowed_user",
    )
    await svc.set_user_mcp_servers("uid1", ["jira", "discord"])
    fga.write.assert_called_once()
    kwargs = fga.write.call_args.kwargs
    assert any(d["object"] == "mcp_server:git" for d in kwargs["deletes"])
    assert any(d["object"] == "mcp_server:slack" for d in kwargs["deletes"])
    assert any(w["object"] == "mcp_server:jira" for w in kwargs["writes"])
    assert any(w["object"] == "mcp_server:discord" for w in kwargs["writes"])


async def test_set_user_mcp_servers_is_noop_when_no_changes():
    svc, fga = _make_svc()
    await svc.set_user_mcp_servers("uid1", [])
    fga.write.assert_not_called()


# ── delete_user_mcp_servers ─────────────────────────────────────────────────────

async def test_delete_user_mcp_servers_removes_all_server_tuples():
    svc, fga = _make_svc()
    fga.read_tuples_by_user.return_value = _fga_tuples(
        ["mcp_server:git", "mcp_server:jira"],
        "user:uid1",
        "allowed_user",
    )
    await svc.delete_user_mcp_servers("uid1")
    fga.write.assert_called_once()
    kwargs = fga.write.call_args.kwargs
    assert any(d["object"] == "mcp_server:git" for d in kwargs["deletes"])
    assert any(d["object"] == "mcp_server:jira" for d in kwargs["deletes"])


async def test_delete_user_mcp_servers_is_noop_when_none_assigned():
    svc, fga = _make_svc()
    await svc.delete_user_mcp_servers("uid1")
    fga.write.assert_not_called()


# ── effective_for_user ──────────────────────────────────────────────────────────

async def test_effective_for_user_returns_intersection_of_role_and_user_perms():
    from services.permissions import UIPermService, expand

    fga = MagicMock()
    fga.write = AsyncMock()

    async def _read_tuples(subject, relation):
        if subject == "user:uid1":
            return _fga_tuples(["ui_permission:chat:model:update"], "user:uid1", "allowed_user")
        if subject == "shokan:shokanllm#member":
            return _fga_tuples(
                ["ui_permission:chat:model:update", "ui_permission:models:menu:read"],
                "shokan:shokanllm#member",
                "allowed_role",
            )
        return []

    fga.read_tuples_by_user = AsyncMock(side_effect=_read_tuples)
    svc = UIPermService(fga)

    result = await svc.effective_for_user("uid1", "member")
    assert "chat:model:update" in result
    assert "models:menu:read" not in result


async def test_effective_for_user_returns_role_perms_when_no_user_perms():
    from services.permissions import UIPermService

    fga = MagicMock()
    fga.write = AsyncMock()

    async def _read_tuples(subject, relation):
        if subject == "shokan:shokanllm#member":
            return _fga_tuples(
                ["ui_permission:chat:model:update"],
                "shokan:shokanllm#member",
                "allowed_role",
            )
        return []

    fga.read_tuples_by_user = AsyncMock(side_effect=_read_tuples)
    svc = UIPermService(fga)

    result = await svc.effective_for_user("uid1", "member")
    assert "chat:model:update" in result
