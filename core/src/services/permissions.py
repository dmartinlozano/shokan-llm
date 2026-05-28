"""UI permission catalog, helpers, and service for Shokan-LLM.

All permissions follow the pattern {section}:{resource}:{action}.
Also includes UIPermService for role/user permission assignments.
"""

from connectors.openfga import OpenFGA

_ACTIONS = ["create", "read", "update", "delete"]

STRUCTURE = {
    "chat": {
        "model":   {"actions": ["update"], "label": "Change model"},
        "history": {"actions": ["delete"], "label": "Delete chats"},
    },
    "models": {
        "menu":    {"actions": ["read"],            "label": "View menu item"},
        "cloud":   {"actions": _ACTIONS,            "label": "Cloud models"},
        "ollama":  {"actions": _ACTIONS + ["start"],"label": "Ollama"},
        "routing": {"actions": _ACTIONS,            "label": "Routing"},
        "a2a":     {"actions": _ACTIONS,            "label": "A2A / Agents"},
        "agents":  {"actions": ["read", "update"],  "label": "Built-in Agents"},
        "skills":  {"actions": _ACTIONS,            "label": "Skills"},
    },
    "datalake": {
        "menu":       {"actions": ["read"], "label": "View menu item"},
        "s3":         {"actions": _ACTIONS, "label": "Amazon S3"},
        "gdrive":     {"actions": _ACTIONS, "label": "Google Drive"},
        "filesystem": {"actions": _ACTIONS, "label": "Filesystem"},
        "sftp":       {"actions": _ACTIONS, "label": "SFTP"},
        "git":        {"actions": _ACTIONS, "label": "Git"},
        "jira":       {"actions": _ACTIONS, "label": "Jira"},
        "confluence": {"actions": _ACTIONS, "label": "Confluence"},
        "slack":      {"actions": _ACTIONS, "label": "Slack"},
        "gmail":      {"actions": _ACTIONS, "label": "Gmail"},
        "discord":    {"actions": _ACTIONS, "label": "Discord"},
    },
    "settings": {
        "menu":     {"actions": ["read"], "label": "View menu item"},
        "users":    {"actions": _ACTIONS, "label": "Users"},
        "groups":   {"actions": _ACTIONS, "label": "Groups"},
        "roles":    {"actions": _ACTIONS, "label": "Roles"},
        "datalake": {"actions": _ACTIONS, "label": "Data Lake perms"},
        "models":   {"actions": _ACTIONS, "label": "Models perms"},
    },
    "system": {
        "menu":       {"actions": ["read"],            "label": "View menu item"},
        "cronjobs":   {"actions": ["read", "update"],  "label": "CronJobs"},
        "rag_index":  {"actions": ["read"],            "label": "Indexed Documents"},
        "rag_params": {"actions": ["read", "update"],  "label": "RAG Parameters"},
        "chats":      {"actions": ["read", "delete"],  "label": "Chat Management"},
        "audit":      {"actions": ["read"],            "label": "Audit Log"},
        "dashboard":  {"actions": ["read"],            "label": "Dashboard"},
        "export":     {"actions": ["read", "write"],   "label": "Export & Import"},
    },
}

SECTION_LABELS = {
    "chat": "Chat",
    "models": "Models",
    "datalake": "Data Lake",
    "settings": "Permissions",
    "system": "System",
}

# Build flat catalog list from STRUCTURE
CATALOG: list[dict] = []
for _sec, _resources in STRUCTURE.items():
    for _res, _spec in _resources.items():
        for _act in _spec["actions"]:
            CATALOG.append({
                "id": f"{_sec}:{_res}:{_act}",
                "section": _sec,
                "resource": _res,
                "action": _act,
                "label": _spec["label"],
            })

ALL_IDS: set[str] = {p["id"] for p in CATALOG}


def expand(perm_ids: list[str]) -> set[str]:
    """Expand ['*'] to all permission IDs."""
    if "*" in perm_ids:
        return set(ALL_IDS)
    return set(perm_ids) & ALL_IDS


def effective(role_perms: list[str] | None, user_perms: list[str] | None) -> set[str]:
    """Most-restrictive: intersection when both are set, otherwise whichever is set."""
    r = expand(role_perms) if role_perms is not None else None
    u = expand(user_perms) if user_perms is not None else None
    if r is not None and u is not None:
        return r & u
    return r if r is not None else (u if u is not None else set())


def can(perms: set[str], perm_id: str) -> bool:
    return perm_id in perms


_DEFAULT_ROLE_PERMS = {
    "admin":  ["*"],
    "member": ["chat:model:update", "models:ollama:start"],
}


class UIPermService:
    """Stores UI permission assignments in OpenFGA.

    Roles: shokan:shokanllm#<role> → allowed_role → ui_permission:<perm_id>
    Users: user:<user_id> → allowed_user → ui_permission:<perm_id>
    """

    def __init__(self, fga: OpenFGA) -> None:
        self._fga = fga

    async def _read_perm_ids(self, subject: str, relation: str) -> list[str]:
        tuples = await self._fga.read_tuples_by_user(subject, relation)
        return [
            t["key"]["object"].removeprefix("ui_permission:")
            for t in tuples
            if t.get("key", {}).get("object", "").startswith("ui_permission:")
        ]

    async def _replace_perms(self, subject: str, relation: str, new_ids: list[str]) -> None:
        old_ids = await self._read_perm_ids(subject, relation)
        deletes = [{"user": subject, "relation": relation, "object": f"ui_permission:{p}"} for p in old_ids] or None
        writes = [{"user": subject, "relation": relation, "object": f"ui_permission:{p}"} for p in new_ids] or None
        if deletes or writes:
            await self._fga.write(writes=writes, deletes=deletes)

    async def get_role_permissions(self, role_name: str) -> list[str] | None:
        perm_ids = await self._read_perm_ids(f"shokan:shokanllm#{role_name}", "allowed_role")
        if not perm_ids:
            return _DEFAULT_ROLE_PERMS.get(role_name)
        return perm_ids

    async def set_role_permissions(self, role_name: str, perm_ids: list[str]) -> None:
        subject = f"shokan:shokanllm#{role_name}"
        if set(perm_ids) >= ALL_IDS:
            # All permissions selected — clear FGA and rely on default wildcard so future
            # permissions added to the catalog are automatically included for this role.
            old_ids = await self._read_perm_ids(subject, "allowed_role")
            if old_ids:
                await self._fga.write(deletes=[
                    {"user": subject, "relation": "allowed_role", "object": f"ui_permission:{p}"}
                    for p in old_ids
                ])
        else:
            await self._replace_perms(subject, "allowed_role", perm_ids)

    async def get_user_permissions(self, user_id: str) -> list[str] | None:
        perm_ids = await self._read_perm_ids(f"user:{user_id}", "allowed_user")
        return perm_ids if perm_ids else None

    async def set_user_permissions(self, user_id: str, perm_ids: list[str]) -> None:
        await self._replace_perms(f"user:{user_id}", "allowed_user", perm_ids)

    async def delete_role_permissions(self, role_name: str) -> None:
        subject = f"shokan:shokanllm#{role_name}"
        old_ids = await self._read_perm_ids(subject, "allowed_role")
        if old_ids:
            await self._fga.write(deletes=[
                {"user": subject, "relation": "allowed_role", "object": f"ui_permission:{p}"}
                for p in old_ids
            ])

    async def get_role_mcp_servers(self, role_name: str) -> list[str]:
        """Return list of mcp_server IDs the role has been granted can_use on."""
        subject = f"shokan:shokanllm#{role_name}"
        tuples = await self._fga.read_tuples_by_user(subject, "allowed_role")
        return [
            t["key"]["object"].removeprefix("mcp_server:")
            for t in tuples
            if t.get("key", {}).get("object", "").startswith("mcp_server:")
        ]

    async def set_role_mcp_servers(self, role_name: str, server_ids: list[str]) -> None:
        """Replace mcp_server allowed_role tuples for a role."""
        subject = f"shokan:shokanllm#{role_name}"
        old_ids = await self.get_role_mcp_servers(role_name)
        deletes = [{"user": subject, "relation": "allowed_role", "object": f"mcp_server:{s}"} for s in old_ids] or None
        writes  = [{"user": subject, "relation": "allowed_role", "object": f"mcp_server:{s}"} for s in server_ids] or None
        if deletes or writes:
            await self._fga.write(writes=writes, deletes=deletes)

    async def delete_role_mcp_servers(self, role_name: str) -> None:
        old_ids = await self.get_role_mcp_servers(role_name)
        if old_ids:
            subject = f"shokan:shokanllm#{role_name}"
            await self._fga.write(deletes=[
                {"user": subject, "relation": "allowed_role", "object": f"mcp_server:{s}"}
                for s in old_ids
            ])

    async def get_user_mcp_servers(self, user_id: str) -> list[str]:
        """Return list of mcp_server IDs the user has been directly granted can_use on."""
        subject = f"user:{user_id}"
        tuples = await self._fga.read_tuples_by_user(subject, "allowed_user")
        return [
            t["key"]["object"].removeprefix("mcp_server:")
            for t in tuples
            if t.get("key", {}).get("object", "").startswith("mcp_server:")
        ]

    async def set_user_mcp_servers(self, user_id: str, server_ids: list[str]) -> None:
        """Replace mcp_server allowed_user tuples for a user."""
        subject = f"user:{user_id}"
        old_ids = await self.get_user_mcp_servers(user_id)
        deletes = [{"user": subject, "relation": "allowed_user", "object": f"mcp_server:{s}"} for s in old_ids] or None
        writes  = [{"user": subject, "relation": "allowed_user", "object": f"mcp_server:{s}"} for s in server_ids] or None
        if deletes or writes:
            await self._fga.write(writes=writes, deletes=deletes)

    async def delete_user_mcp_servers(self, user_id: str) -> None:
        old_ids = await self.get_user_mcp_servers(user_id)
        if old_ids:
            await self._fga.write(deletes=[
                {"user": f"user:{user_id}", "relation": "allowed_user", "object": f"mcp_server:{s}"}
                for s in old_ids
            ])

    async def delete_user_permissions(self, user_id: str) -> None:
        old_ids = await self._read_perm_ids(f"user:{user_id}", "allowed_user")
        if old_ids:
            await self._fga.write(deletes=[
                {"user": f"user:{user_id}", "relation": "allowed_user", "object": f"ui_permission:{p}"}
                for p in old_ids
            ])

    async def effective_for_user(
        self,
        user_id: str,
        direct_role: str | None,
        group_roles: list[str] | None = None,
    ) -> set[str]:
        user_list = await self.get_user_permissions(user_id)

        if direct_role:
            role_list = await self.get_role_permissions(direct_role)
            base_set: set[str] | None = expand(role_list) if role_list is not None else None
        elif group_roles:
            base: set[str] = set()
            for r in group_roles:
                r_perms = await self.get_role_permissions(r)
                if r_perms:
                    base = base | expand(r_perms)
            base_set = base if base else None
        else:
            base_set = None

        return effective(list(base_set) if base_set is not None else None, user_list)
