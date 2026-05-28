"""OpenFGA authorization client."""

import os

import httpx

SHOKAN_OBJECT = "shokan:shokanllm"


class OpenFGA:
    """Async client for OpenFGA authorization checks and tuple management.

    Reads OPENFGA_URL and OPENFGA_STORE_ID from env.
    When OPENFGA_STORE_ID is unset, all checks return True (dev mode).
    """

    def __init__(self) -> None:
        self.url = os.getenv("OPENFGA_URL", "http://openfga.shokanllm.svc.cluster.local:8080")
        self.store_id = os.getenv("OPENFGA_STORE_ID", "")

    def _endpoint(self, path: str) -> str:
        return f"{self.url}/stores/{self.store_id}/{path}"

    async def check(self, user_id: str, relation: str, object_id: str) -> bool:
        """Return True if user has relation on object_id."""
        if not self.store_id:
            return True
        async with httpx.AsyncClient() as http:
            try:
                r = await http.post(
                    self._endpoint("check"),
                    json={
                        "tuple_key": {
                            "user": f"user:{user_id}",
                            "relation": relation,
                            "object": object_id,
                        }
                    },
                    timeout=5.0,
                )
                return r.is_success and r.json().get("allowed", False)
            except Exception:
                return False

    async def read_tuples(self, object_id: str) -> list[dict]:
        """Return all tuples for a given object."""
        if not self.store_id:
            return []
        async with httpx.AsyncClient() as http:
            try:
                r = await http.post(
                    self._endpoint("read"),
                    json={"tuple_key": {"object": object_id}},
                    timeout=5.0,
                )
                return r.json().get("tuples", []) if r.is_success else []
            except Exception:
                return []

    async def read_all_tuples(self) -> list[dict]:
        """Return all tuples in the store as [{user, relation, object}], paginated."""
        if not self.store_id:
            return []
        all_tuples = []
        continuation_token = None
        async with httpx.AsyncClient() as http:
            while True:
                body: dict = {}
                if continuation_token:
                    body["continuation_token"] = continuation_token
                try:
                    r = await http.post(self._endpoint("read"), json=body, timeout=10.0)
                    if not r.is_success:
                        break
                    data = r.json()
                except Exception:
                    break
                for t in data.get("tuples", []):
                    key = t.get("key", {})
                    if key.get("user") and key.get("relation") and key.get("object"):
                        all_tuples.append({"user": key["user"], "relation": key["relation"], "object": key["object"]})
                continuation_token = data.get("continuation_token", "")
                if not continuation_token:
                    break
        return all_tuples

    async def read_tuples_by_user(self, user: str, relation: str) -> list[dict]:
        """Return all tuples where user+relation match (any object)."""
        if not self.store_id:
            return []
        async with httpx.AsyncClient() as http:
            try:
                r = await http.post(
                    self._endpoint("read"),
                    json={"tuple_key": {"user": user, "relation": relation}},
                    timeout=5.0,
                )
                return r.json().get("tuples", []) if r.is_success else []
            except Exception:
                return []

    async def write(
        self,
        writes: list[dict] | None = None,
        deletes: list[dict] | None = None,
    ) -> None:
        """Write and/or delete tuples. Raises httpx.HTTPStatusError on API errors."""
        if not self.store_id or (not writes and not deletes):
            return
        body: dict = {}
        if writes:
            body["writes"] = {"tuple_keys": writes}
        if deletes:
            body["deletes"] = {"tuple_keys": deletes}
        async with httpx.AsyncClient() as http:
            r = await http.post(self._endpoint("write"), json=body, timeout=5.0)
            if r.status_code == 409:
                return  # tuple already exists — idempotent
            r.raise_for_status()

    async def get_object_tuples(self, object_id: str) -> dict[str, str]:
        """Return {user_ref: relation} for all tuples on an object (one per user)."""
        tuples = await self.read_tuples(object_id)
        return {
            t["key"]["user"]: t["key"]["relation"]
            for t in tuples
            if t.get("key", {}).get("object") == object_id
        }

    async def get_object_tuples_multi(self, object_id: str) -> dict[str, list[str]]:
        """Return {user_ref: [relations]} for all tuples on an object (multiple per user)."""
        tuples = await self.read_tuples(object_id)
        result: dict[str, list[str]] = {}
        for t in tuples:
            if t.get("key", {}).get("object") == object_id:
                user = t["key"]["user"]
                rel = t["key"]["relation"]
                result.setdefault(user, []).append(rel)
        return result

    async def set_relation(
        self, subject: str, new_relation: str, old_relation: str | None, object_id: str
    ) -> None:
        """Replace old_relation with new_relation for subject on object_id."""
        writes = [{"user": subject, "relation": new_relation, "object": object_id}]
        deletes = None
        if old_relation and old_relation != new_relation:
            deletes = [{"user": subject, "relation": old_relation, "object": object_id}]
        await self.write(writes=writes, deletes=deletes)

    async def remove_relation(self, subject: str, relation: str, object_id: str) -> None:
        """Remove a specific relation tuple."""
        if relation:
            await self.write(deletes=[{"user": subject, "relation": relation, "object": object_id}])

    async def purge_subject(self, subject: str) -> None:
        """Delete all FGA tuples where user == subject (best-effort sweep across known relations)."""
        if not self.store_id:
            return
        known_relations = [
            "admin", "member",
            "allowed_user", "shokan",
            "can_read", "can_write", "can_use", "can_manage",
        ]
        for relation in known_relations:
            try:
                tuples = await self.read_tuples_by_user(subject, relation)
                to_delete = [
                    {"user": t["key"]["user"], "relation": t["key"]["relation"], "object": t["key"]["object"]}
                    for t in tuples
                    if t.get("key")
                ]
                if to_delete:
                    await self.write(deletes=to_delete)
            except Exception:
                pass
