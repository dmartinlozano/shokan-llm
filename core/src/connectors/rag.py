"""RAG datasource configuration, access management, and retrieval."""

import asyncio
import os

import httpx
from qdrant_client import QdrantClient

from config import OLLAMA_URL as _OLLAMA_URL
from connectors.k8s import K8s
from connectors.openfga import SHOKAN_OBJECT, OpenFGA

_COLLECTION = "shokan_rag"

DATASOURCE_TYPES = ["gdrive", "s3", "filesystem", "other"]


class RAG:
    """Manage RAG datasource registry (K8s secret) and access control (OpenFGA).

    datasource-config schema: {"datasources": [{id, name, type, enabled}]}
    FGA objects: datasource:<id>
    """

    def __init__(self, k8s: K8s, fga: OpenFGA) -> None:
        self.k8s = k8s
        self.fga = fga

    # ── Datasource registry ────────────────────────────────────────────────────

    def list_datasources(self) -> list[dict]:
        return self.k8s.read_json("datasource-config").get("datasources", [])

    async def add_datasource(self, ds_id: str, name: str, ds_type: str) -> None:
        """Register a datasource and write its structural FGA tuple."""
        cfg = await asyncio.to_thread(self.k8s.read_json, "datasource-config")
        cfg.setdefault("datasources", []).append(
            {"id": ds_id, "name": name or ds_id, "type": ds_type, "enabled": True}
        )
        await asyncio.to_thread(self.k8s.write_json, "datasource-config", cfg)
        # Link datasource to the platform so admins inherit access
        await self.fga.write(
            writes=[
                {"user": SHOKAN_OBJECT, "relation": "shokan", "object": f"datasource:{ds_id}"}
            ]
        )

    async def remove_datasource(self, ds_id: str) -> None:
        cfg = await asyncio.to_thread(self.k8s.read_json, "datasource-config")
        cfg["datasources"] = [d for d in cfg.get("datasources", []) if d.get("id") != ds_id]
        await asyncio.to_thread(self.k8s.write_json, "datasource-config", cfg)
        # Remove structural tuple and all per-user access tuples
        try:
            tuples = await self.fga.read_tuples(f"datasource:{ds_id}")
            if tuples:
                to_delete = [
                    {"user": t["key"]["user"], "relation": t["key"]["relation"], "object": t["key"]["object"]}
                    for t in tuples
                ]
                await self.fga.write(deletes=to_delete)
            else:
                await self.fga.remove_relation(SHOKAN_OBJECT, "shokan", f"datasource:{ds_id}")
        except Exception:
            pass

    # ── FGA access control ────────────────────────────────────────────────────

    async def get_datasource_access(self, ds_id: str) -> dict[str, str]:
        """Return {user_ref: relation} for datasource:<ds_id>."""
        return await self.fga.get_object_tuples(f"datasource:{ds_id}")

    async def grant_access(self, ds_id: str, subject: str, relation: str) -> None:
        await self.fga.write(
            writes=[{"user": subject, "relation": relation, "object": f"datasource:{ds_id}"}]
        )

    async def revoke_access(self, ds_id: str, subject: str, relation: str) -> None:
        await self.fga.remove_relation(subject, relation, f"datasource:{ds_id}")


# ── Qdrant scroll helper ──────────────────────────────────────────────────────


def scroll_qdrant_index(qdrant) -> list[dict]:
    """Return one entry per unique file_path with its chunk count."""
    from qdrant_client.http.exceptions import UnexpectedResponse

    counts: dict[tuple, dict] = {}
    offset = None

    while True:
        try:
            points, next_offset = qdrant.scroll(
                collection_name=_COLLECTION,
                offset=offset,
                limit=250,
                with_payload=["datasource_id", "source_type", "file_path"],
                with_vectors=False,
            )
        except UnexpectedResponse as exc:
            if "Not found" in str(exc) or exc.status_code == 404:
                return []
            raise
        except Exception:
            return []

        for p in points:
            pl  = p.payload or {}
            key = (pl.get("datasource_id", ""), pl.get("source_type", ""), pl.get("file_path", ""))
            if key not in counts:
                counts[key] = {
                    "datasource_id": key[0],
                    "source_type":   key[1],
                    "file_path":     key[2],
                    "chunks":        0,
                }
            counts[key]["chunks"] += 1

        if next_offset is None:
            break
        offset = next_offset

    return sorted(counts.values(), key=lambda d: (d["source_type"], d["file_path"]))


# ── Retriever ─────────────────────────────────────────────────────────────────

QDRANT_URL  = os.getenv("QDRANT_URL",  "http://qdrant.shokanllm.svc.cluster.local:6333")
OLLAMA_URL  = _OLLAMA_URL
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
COLLECTION  = "shokan_rag"

_top_k: list[int] = [int(os.getenv("RAG_TOP_K", "5"))]


def get_top_k() -> int:
    return _top_k[0]


def set_top_k(value: int) -> None:
    _top_k[0] = max(1, int(value))


class Retriever:
    """Embeds a query via Ollama and retrieves the top-K relevant chunks from Qdrant."""

    def __init__(self) -> None:
        self._qdrant = QdrantClient(url=QDRANT_URL)
        self._fga = OpenFGA()

    def _collection_exists(self) -> bool:
        try:
            self._qdrant.get_collection(COLLECTION)
            return True
        except Exception:
            return False

    async def retrieve(self, query: str, user_id: str | None = None, top_k: int | None = None) -> list[str]:
        """Return relevant text chunks for query, filtered by user's datasource permissions.

        Returns [] if RAG is unavailable or collection not yet created (before first ingest).
        """
        k = top_k if top_k is not None else get_top_k()
        if not await asyncio.to_thread(self._collection_exists):
            return []
        try:
            async with httpx.AsyncClient() as http:
                r = await http.post(
                    f"{OLLAMA_URL}/api/embed",
                    json={"model": EMBED_MODEL, "input": query},
                    timeout=15.0,
                )
                r.raise_for_status()
                vector = r.json()["embeddings"][0]

            # Fetch extra candidates to compensate for permission filtering
            fetch_k = k * 4 if user_id else k
            hits = await asyncio.to_thread(
                self._qdrant.search,
                collection_name=COLLECTION,
                query_vector=vector,
                limit=fetch_k,
                with_payload=True,
            )

            if user_id and hits:
                ds_ids = list({h.payload.get("datasource_id") for h in hits if h.payload.get("datasource_id")})
                checks = await asyncio.gather(*[
                    self._fga.check(user_id, "can_read", f"datasource:{ds_id}")
                    for ds_id in ds_ids
                ])
                allowed = {ds_id for ds_id, ok in zip(ds_ids, checks) if ok}
                hits = [h for h in hits if h.payload.get("datasource_id") in allowed][:k]

            return [h.payload["text"] for h in hits if h.payload.get("text")]
        except Exception:
            return []
