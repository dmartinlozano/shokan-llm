"""Keycloak admin API client."""

import os

import httpx

from config import KC_REALM, KC_URL


class KeycloakError(Exception):
    """Raised when Keycloak is unreachable or returns an auth error."""


class Keycloak:
    """Async client for Keycloak admin REST API.

    Reads KC_ADMIN_PASSWORD from env. Uses admin-cli grant_type=password.
    All HTTP calls go to the internal K8s ClusterIP (no TLS needed).
    """

    def __init__(self) -> None:
        self.url = KC_URL
        self.realm = KC_REALM
        self.admin_user = os.getenv("KC_ADMIN_USER", "admin")
        self.admin_pass = os.getenv("KC_ADMIN_PASSWORD", "")

    def _admin_url(self, path: str) -> str:
        return f"{self.url}/admin/realms/{self.realm}/{path}"

    def _token_url(self) -> str:
        return f"{self.url}/realms/{self.realm}/protocol/openid-connect/token"

    async def admin_token(self) -> str:
        """Obtain a short-lived admin token via Resource Owner Password grant."""
        try:
            async with httpx.AsyncClient() as http:
                r = await http.post(
                    self._token_url(),
                    data={
                        "client_id": "admin-cli",
                        "username": self.admin_user,
                        "password": self.admin_pass,
                        "grant_type": "password",
                    },
                    timeout=10.0,
                )
                r.raise_for_status()
                return r.json()["access_token"]
        except httpx.HTTPStatusError as exc:
            raise KeycloakError(
                f"Keycloak auth failed ({exc.response.status_code}) — check KC_ADMIN_PASSWORD."
            ) from exc
        except Exception as exc:
            raise KeycloakError(f"Keycloak unreachable: {exc}") from exc

    async def list_users(self, max_results: int = 200) -> list[dict]:
        """Return all realm users (id, username, email, enabled)."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.get(
                self._admin_url(f"users?max={max_results}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def list_groups(self, max_results: int = 200) -> list[dict]:
        """Return all realm groups (id, name, path)."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.get(
                self._admin_url(f"groups?max={max_results}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def get_user(self, user_id: str) -> dict:
        """Return a single user by Keycloak UUID."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.get(
                self._admin_url(f"users/{user_id}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def create_user(
        self,
        username: str,
        email: str = "",
        first_name: str = "",
        last_name: str = "",
        password: str = "",
    ) -> str:
        """Create a user and return its new UUID."""
        token = await self.admin_token()
        body: dict = {
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
        }
        if password:
            body["credentials"] = [{"type": "password", "value": password, "temporary": False}]
        async with httpx.AsyncClient() as http:
            r = await http.post(
                self._admin_url("users"),
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            uid = r.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
            if not uid:
                raise KeycloakError("Keycloak did not return a Location header for new user")
            return uid

    async def update_user(
        self,
        user_id: str,
        username: str | None = None,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        """Merge changed fields into the existing user representation and PUT."""
        current = await self.get_user(user_id)
        if username is not None:
            current["username"] = username
        if email is not None:
            current["email"] = email
        if first_name is not None:
            current["firstName"] = first_name
        if last_name is not None:
            current["lastName"] = last_name
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.put(
                self._admin_url(f"users/{user_id}"),
                json=current,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()

    async def set_user_password(self, user_id: str, password: str, temporary: bool = False) -> None:
        """Reset a user's password."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.put(
                self._admin_url(f"users/{user_id}/reset-password"),
                json={"type": "password", "value": password, "temporary": temporary},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()

    async def delete_user(self, user_id: str) -> None:
        """Delete a user by UUID."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.delete(
                self._admin_url(f"users/{user_id}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()

    async def create_group(self, name: str) -> str:
        """Create a group and return its new UUID."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.post(
                self._admin_url("groups"),
                json={"name": name},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            gid = r.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
            if not gid:
                raise KeycloakError("Keycloak did not return a Location header for new group")
            return gid

    async def update_group(self, group_id: str, name: str) -> None:
        """Rename a group."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.put(
                self._admin_url(f"groups/{group_id}"),
                json={"name": name},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()

    async def delete_group(self, group_id: str) -> None:
        """Delete a group by UUID."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.delete(
                self._admin_url(f"groups/{group_id}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()

    async def list_user_groups(self, user_id: str) -> list[dict]:
        """Return groups the user belongs to (id, name, path)."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.get(
                self._admin_url(f"users/{user_id}/groups"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()

    async def list_group_members(self, group_id: str, max_results: int = 200) -> list[dict]:
        """Return users who are members of this group."""
        token = await self.admin_token()
        async with httpx.AsyncClient() as http:
            r = await http.get(
                self._admin_url(f"groups/{group_id}/members?max={max_results}"),
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            r.raise_for_status()
            return r.json()
