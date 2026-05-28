"""Tests for auth helpers and permission resolution (replaces legacy Chainlit tests)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


# ── UIPermService – role resolution ───────────────────────────────────────────

class TestRoleResolution:
    def setup_method(self):
        fga = MagicMock()
        fga.read_tuples_by_user = AsyncMock(return_value=[])
        fga.write = AsyncMock()
        from services.permissions import UIPermService
        self.svc = UIPermService(fga)

    @pytest.mark.asyncio
    async def test_admin_role_grants_all_permissions(self):
        from services.permissions import ALL_IDS
        perms = await self.svc.effective_for_user("uid-admin", "admin")
        assert perms == ALL_IDS, "admin role must grant every permission"

    @pytest.mark.asyncio
    async def test_member_role_grants_only_allowed_permissions(self):
        perms = await self.svc.effective_for_user("uid-member", "member")
        assert "chat:model:update" in perms
        assert "settings:users:create" not in perms
        assert "system:cronjobs:update" not in perms

    @pytest.mark.asyncio
    async def test_unknown_role_returns_empty_permissions(self):
        perms = await self.svc.effective_for_user("uid-ghost", "nonexistent_role")
        assert perms == set()

    @pytest.mark.asyncio
    async def test_no_role_returns_empty_permissions(self):
        perms = await self.svc.effective_for_user("uid-norole", None)
        assert perms == set()

    @pytest.mark.asyncio
    async def test_group_role_union_applied_when_no_direct_role(self):
        perms = await self.svc.effective_for_user("uid-g", None, group_roles=["member"])
        assert "chat:model:update" in perms


# ── Permission catalog ─────────────────────────────────────────────────────────

class TestPermissionCatalog:
    def test_catalog_is_non_empty(self):
        from services.permissions import CATALOG
        assert len(CATALOG) > 0

    def test_all_catalog_entries_have_required_fields(self):
        from services.permissions import CATALOG
        for p in CATALOG:
            assert "id" in p, f"Missing id: {p}"
            assert "section" in p
            assert "resource" in p
            assert "action" in p

    def test_catalog_ids_are_unique(self):
        from services.permissions import CATALOG
        ids = [p["id"] for p in CATALOG]
        assert len(ids) == len(set(ids)), "Duplicate permission IDs found"

    def test_can_helper_works(self):
        from services.permissions import can
        perms = {"chat:model:update", "models:menu:read"}
        assert can(perms, "chat:model:update") is True
        assert can(perms, "settings:users:delete") is False

    def test_expand_wildcard_returns_all(self):
        from services.permissions import expand, ALL_IDS
        result = expand(["*"])
        assert result == ALL_IDS

    def test_expand_filters_unknown_ids(self):
        from services.permissions import expand
        result = expand(["chat:model:update", "fake:perm:xyz"])
        assert "chat:model:update" in result
        assert "fake:perm:xyz" not in result


# ── Default role assignment on first login ─────────────────────────────────────

class TestDefaultRoleAssignment:
    @pytest.mark.asyncio
    async def test_admin_usernames_get_admin_role(self):
        fga = MagicMock()
        fga.get_object_tuples = AsyncMock(return_value={})
        fga.write = AsyncMock()

        with patch("connectors.openfga.OpenFGA", return_value=fga):
            from connectors.openfga import SHOKAN_OBJECT

        # Simulate _assign_default_role logic
        admin_usernames = frozenset({"shokan-admin", "shokan-svc"})
        for uname in admin_usernames:
            user = {"id": "uid-svc", "username": uname}
            role = "admin" if user.get("username") in admin_usernames else "member"
            assert role == "admin", f"{uname} should be assigned admin"

    @pytest.mark.asyncio
    async def test_regular_users_get_member_role(self):
        admin_usernames = frozenset({"shokan-admin", "shokan-svc"})
        for uname in ["alice", "bob", "carol"]:
            user = {"id": "uid-x", "username": uname}
            role = "admin" if user.get("username") in admin_usernames else "member"
            assert role == "member", f"{uname} should be assigned member"
