"""Group management — Keycloak identity + OpenFGA roles (multi-role support)."""

import asyncio

from connectors.keycloak import Keycloak
from connectors.openfga import SHOKAN_OBJECT, OpenFGA

_PLATFORM_ROLES = ["admin", "member"]


class GroupService:
    """Combines Keycloak group CRUD with OpenFGA platform-role management.

    A group can hold multiple roles. Members inherit all group roles unless
    they have a direct role assigned, in which case the direct role takes priority.
    """

    def __init__(self, kc: Keycloak, fga: OpenFGA) -> None:
        self.kc = kc
        self.fga = fga

    async def list_with_roles(self) -> list[dict]:
        groups, tuples_multi = await asyncio.gather(
            self.kc.list_groups(),
            self.fga.get_object_tuples_multi(SHOKAN_OBJECT),
        )
        return [
            {
                "name":  g.get("name", g["id"]),
                "roles": tuples_multi.get(f"group:{g['id']}#member", []),
                "_id":   g["id"],
            }
            for g in groups
        ]

    async def create(self, name: str, roles: list[str]) -> str:
        new_id = await self.kc.create_group(name)
        valid = [r for r in roles if r in _PLATFORM_ROLES]
        if valid:
            subj = f"group:{new_id}#member"
            await self.fga.write(
                writes=[{"user": subj, "relation": r, "object": SHOKAN_OBJECT} for r in valid]
            )
        return new_id

    async def update(self, group_id: str, name: str, new_roles: list[str]) -> None:
        await self.kc.update_group(group_id, name)
        subj = f"group:{group_id}#member"
        tuples_multi = await self.fga.get_object_tuples_multi(SHOKAN_OBJECT)
        current = set(tuples_multi.get(subj, []))
        desired = {r for r in new_roles if r in _PLATFORM_ROLES}
        to_add = desired - current
        to_del = current - desired
        writes  = [{"user": subj, "relation": r, "object": SHOKAN_OBJECT} for r in to_add]
        deletes = [{"user": subj, "relation": r, "object": SHOKAN_OBJECT} for r in to_del]
        if writes or deletes:
            await self.fga.write(
                writes=writes or None,
                deletes=deletes or None,
            )

    async def delete(self, group_id: str) -> None:
        # Ensure group members without a direct role are assigned 'member'
        try:
            members, tuples = await asyncio.gather(
                self.kc.list_group_members(group_id),
                self.fga.get_object_tuples(SHOKAN_OBJECT),
            )
            for m in members:
                uid = m.get("id", "")
                if uid and not tuples.get(f"user:{uid}"):
                    await self.fga.set_relation(f"user:{uid}", "member", None, SHOKAN_OBJECT)
        except Exception:
            pass

        # Remove all FGA tuples for this group
        subj = f"group:{group_id}#member"
        tuples_multi = await self.fga.get_object_tuples_multi(SHOKAN_OBJECT)
        current = tuples_multi.get(subj, [])
        if current:
            await self.fga.write(
                deletes=[{"user": subj, "relation": r, "object": SHOKAN_OBJECT} for r in current]
            )

        await self.kc.delete_group(group_id)
