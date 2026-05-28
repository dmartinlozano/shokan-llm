"""User management — Keycloak identity + OpenFGA platform roles."""

import asyncio

from connectors.keycloak import Keycloak
from connectors.openfga import SHOKAN_OBJECT, OpenFGA

_PLATFORM_ROLES = ["admin", "member"]


class UserService:
    """Combines Keycloak user CRUD with OpenFGA platform-role management."""

    def __init__(self, kc: Keycloak, fga: OpenFGA) -> None:
        self.kc = kc
        self.fga = fga

    async def list_with_roles(self) -> list[dict]:
        """Return all users with their current platform role."""
        users, tuples = await asyncio.gather(
            self.kc.list_users(),
            self.fga.get_object_tuples(SHOKAN_OBJECT),
        )
        for u in users:
            if u.get("username") == self.kc.admin_user:
                subj = f"user:{u['id']}"
                if tuples.get(subj) not in _PLATFORM_ROLES:
                    await self.fga.set_relation(subj, "admin", None, SHOKAN_OBJECT)
                    tuples[subj] = "admin"
        return [
            {
                "username": u.get("username", u["id"]),
                "email": u.get("email", ""),
                "role": tuples.get(f"user:{u['id']}", "member"),
                "_id": u["id"],
                "_firstname": u.get("firstName", ""),
                "_lastname": u.get("lastName", ""),
            }
            for u in users
        ]

    async def create(
        self,
        username: str,
        email: str,
        firstname: str,
        lastname: str,
        password: str,
        role: str,
    ) -> str:
        new_id = await self.kc.create_user(username, email, firstname, lastname, password)
        if role in _PLATFORM_ROLES:
            await self.fga.set_relation(f"user:{new_id}", role, None, SHOKAN_OBJECT)
        return new_id

    async def update(
        self,
        user_id: str,
        username: str,
        email: str,
        firstname: str,
        lastname: str,
        password: str,
        new_role: str,
    ) -> None:
        await self.kc.update_user(
            user_id,
            username=username or None,
            email=email or None,
            first_name=firstname or None,
            last_name=lastname or None,
        )
        if password:
            await self.kc.set_user_password(user_id, password)
        tuples = await self.fga.get_object_tuples(SHOKAN_OBJECT)
        prev_role = tuples.get(f"user:{user_id}")
        if new_role != prev_role:
            await self.fga.set_relation(f"user:{user_id}", new_role, prev_role, SHOKAN_OBJECT)

    async def delete(self, user_id: str) -> None:
        await self.kc.delete_user(user_id)
        try:
            await self.fga.purge_subject(f"user:{user_id}")
        except Exception:
            pass
