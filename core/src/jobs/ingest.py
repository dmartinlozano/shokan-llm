#!/usr/bin/env python3
"""RAG ingestion job — runs as a Kubernetes CronJob.

Reads all enabled data sources from the K8s secret config,
downloads/reads files, chunks them, embeds via Ollama, and
upserts into Qdrant. Idempotent: re-running overwrites existing chunks.
"""

import asyncio
import hashlib
import io
import logging
import os
import uuid

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointIdsList, PointStruct, VectorParams

from connectors.k8s import K8s

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

OLLAMA_URL    = os.getenv("OLLAMA_URL",  "http://ollama.shokanllm.svc.cluster.local:11434")
QDRANT_URL    = os.getenv("QDRANT_URL",  "http://qdrant.shokanllm.svc.cluster.local:6333")
EMBED_MODEL   = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE",    "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
COLLECTION    = "shokan_rag"

_TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".html", ".xml",
    ".pdf", ".docx",
}


# ── Text extraction ────────────────────────────────────────────────────────────

def extract_text(content: bytes, filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            log.warning("PDF extraction failed for %s: %s", filename, exc)
            return ""
    if name.endswith(".docx"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as exc:
            log.warning("DOCX extraction failed for %s: %s", filename, exc)
            return ""
    return content.decode("utf-8", errors="replace")


def _indexable(name: str) -> bool:
    return any(name.lower().endswith(ext) for ext in _TEXT_EXTS)


# ── Chunking ───────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if c.strip()]


# ── Embedding ──────────────────────────────────────────────────────────────────

async def embed(http: httpx.AsyncClient, text: str) -> list[float]:
    r = await http.post(
        f"{OLLAMA_URL}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()["embeddings"][0]


# ── Qdrant ─────────────────────────────────────────────────────────────────────

def _ensure_collection(qdrant: QdrantClient, dim: int) -> None:
    existing = {c.name for c in qdrant.get_collections().collections}
    if COLLECTION not in existing:
        qdrant.create_collection(
            COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection '%s' (dim=%d)", COLLECTION, dim)


def _point_id(datasource_id: str, file_path: str, chunk_idx: int) -> str:
    raw = f"{datasource_id}|{file_path}|{chunk_idx}"
    return str(uuid.UUID(hashlib.md5(raw.encode()).hexdigest()))


async def ingest_file(
    qdrant: QdrantClient,
    http: httpx.AsyncClient,
    datasource_id: str,
    source_type: str,
    file_path: str,
    content: bytes,
) -> tuple[int, set[str]]:
    text = extract_text(content, file_path)
    if not text.strip():
        return 0, set()
    chunks = chunk_text(text)
    points: list[PointStruct] = []
    for i, chunk in enumerate(chunks):
        vector = await embed(http, chunk)
        points.append(PointStruct(
            id=_point_id(datasource_id, file_path, i),
            vector=vector,
            payload={
                "datasource_id": datasource_id,
                "source_type":   source_type,
                "file_path":     file_path,
                "chunk_index":   i,
                "text":          chunk,
            },
        ))
    if points:
        await asyncio.to_thread(_ensure_collection, qdrant, len(points[0].vector))
        await asyncio.to_thread(qdrant.upsert, COLLECTION, points)
    return len(points), {str(p.id) for p in points}


def _delete_stale_points(qdrant: QdrantClient, datasource_id: str, valid_ids: set[str]) -> int:
    """Delete Qdrant points for datasource_id that are no longer in valid_ids."""
    from qdrant_client.http.exceptions import UnexpectedResponse
    existing: set[str] = set()
    offset = None
    while True:
        try:
            points, next_offset = qdrant.scroll(
                collection_name=COLLECTION,
                offset=offset,
                limit=250,
                with_payload=False,
                with_vectors=False,
                scroll_filter=Filter(
                    must=[FieldCondition(key="datasource_id", match=MatchValue(value=datasource_id))]
                ),
            )
        except (UnexpectedResponse, Exception):
            return 0
        for p in points:
            existing.add(str(p.id))
        if next_offset is None:
            break
        offset = next_offset

    stale = existing - valid_ids
    if stale:
        qdrant.delete(COLLECTION, points_selector=PointIdsList(points=list(stale)))
        log.info("Deleted %d stale chunk(s) for datasource '%s'", len(stale), datasource_id)
    return len(stale)


# ── Google Drive ───────────────────────────────────────────────────────────────

async def ingest_gdrive(qdrant: QdrantClient, http: httpx.AsyncClient, k8s: K8s, source: dict) -> int:
    cred_id = source.get("credential_id", "")
    meta = next((c for c in k8s.read_json("rag-gdrive-credentials").get("credentials", []) if c["id"] == cred_id), None)
    if not meta:
        log.warning("GDrive %s: credential %s not found, skipping", source["id"], cred_id)
        return 0

    client_id     = meta.get("client_id", "")
    client_secret = k8s.read(f"rag-gdrive-cred-{cred_id}-client-secret")
    refresh_token = k8s.read(f"rag-gdrive-cred-{cred_id}-refresh-token")
    if not all([client_id, client_secret, refresh_token]):
        log.warning("GDrive %s: incomplete credentials, skipping", source["id"])
        return 0

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id, client_secret=client_secret,
    )
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    folder_id = source.get("folder_id", "root")
    files = await asyncio.to_thread(_gdrive_list, service, folder_id)
    total = 0
    valid_ids: set[str] = set()
    for f in files:
        if not _indexable(f["name"]):
            continue
        try:
            content = await asyncio.to_thread(_gdrive_download, service, f["id"])
            n, ids = await ingest_file(qdrant, http, source["id"], "gdrive", f"gdrive:{f['id']}:{f['name']}", content)
            total += n
            valid_ids.update(ids)
            log.info("GDrive %s: %d chunks", f["name"], n)
        except Exception as exc:
            log.warning("GDrive %s: %s", f["name"], exc)
    await asyncio.to_thread(_delete_stale_points, qdrant, source["id"], valid_ids)
    return total


def _gdrive_list(service, folder_id: str) -> list[dict]:
    results, page_token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            pageSize=100,
            fields="nextPageToken,files(id,name,mimeType)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                results.extend(_gdrive_list(service, f["id"]))
            else:
                results.append(f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def _gdrive_download(service, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, service.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()


# ── Amazon S3 ──────────────────────────────────────────────────────────────────

async def ingest_s3(qdrant: QdrantClient, http: httpx.AsyncClient, k8s: K8s, source: dict) -> int:
    import boto3

    cred_id = source.get("credential_id", "")
    meta = next((c for c in k8s.read_json("rag-s3-credentials").get("credentials", []) if c["id"] == cred_id), None)
    if not meta:
        log.warning("S3 %s: credential %s not found, skipping", source["id"], cred_id)
        return 0

    secret_key = k8s.read(f"rag-s3-cred-{cred_id}-secret-key")
    kwargs: dict = dict(
        aws_access_key_id=meta.get("access_key_id", ""),
        aws_secret_access_key=secret_key,
        region_name=meta.get("region", "us-east-1"),
    )
    if meta.get("endpoint"):
        kwargs["endpoint_url"] = meta["endpoint"]

    s3 = boto3.client("s3", **kwargs)
    bucket = source.get("bucket", "")
    prefix = source.get("prefix", "")
    paginator = s3.get_paginator("list_objects_v2")
    total = 0
    valid_ids: set[str] = set()

    def _list_objects() -> list[dict]:
        objs = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objs.extend(page.get("Contents", []))
        return objs

    objects = await asyncio.to_thread(_list_objects)
    for obj in objects:
        key = obj["Key"]
        if not _indexable(key):
            continue
        try:
            content = await asyncio.to_thread(
                lambda k=key: s3.get_object(Bucket=bucket, Key=k)["Body"].read()
            )
            n, ids = await ingest_file(qdrant, http, source["id"], "s3", f"s3://{bucket}/{key}", content)
            total += n
            valid_ids.update(ids)
            log.info("S3 %s: %d chunks", key, n)
        except Exception as exc:
            log.warning("S3 %s: %s", key, exc)
    await asyncio.to_thread(_delete_stale_points, qdrant, source["id"], valid_ids)
    return total


# ── Filesystem ─────────────────────────────────────────────────────────────────

async def ingest_filesystem(qdrant: QdrantClient, http: httpx.AsyncClient, volume: dict) -> int:
    scan_path = volume.get("scan_path", "/")
    if not os.path.isdir(scan_path):
        log.warning("Filesystem %s: path %s not mounted, skipping", volume["id"], scan_path)
        return 0
    total = 0
    valid_ids: set[str] = set()
    for root, _, files in os.walk(scan_path):
        for fname in files:
            if not _indexable(fname):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                n, ids = await ingest_file(qdrant, http, volume["id"], "filesystem", fpath, content)
                total += n
                valid_ids.update(ids)
                log.info("FS %s: %d chunks", fpath, n)
            except Exception as exc:
                log.warning("FS %s: %s", fpath, exc)
    await asyncio.to_thread(_delete_stale_points, qdrant, volume["id"], valid_ids)
    return total


# ── SFTP ───────────────────────────────────────────────────────────────────────

async def ingest_sftp(qdrant: QdrantClient, http: httpx.AsyncClient, k8s: K8s, conn: dict) -> int:
    import stat
    import paramiko

    cred_id = conn.get("credential_id", "")
    meta = next((c for c in k8s.read_json("rag-sftp-credentials").get("credentials", []) if c["id"] == cred_id), None)
    if not meta:
        log.warning("SFTP %s: credential %s not found, skipping", conn["id"], cred_id)
        return 0

    password    = k8s.read(f"rag-sftp-cred-{cred_id}-password")
    private_key = k8s.read(f"rag-sftp-cred-{cred_id}-private-key")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {"username": meta.get("username", ""), "timeout": 30}
    if private_key:
        kwargs["pkey"] = paramiko.RSAKey.from_private_key(io.StringIO(private_key))
    elif password:
        kwargs["password"] = password

    total = 0
    try:
        await asyncio.to_thread(ssh.connect, conn.get("host", ""), port=int(conn.get("port", 22)), **kwargs)
        sftp = await asyncio.to_thread(ssh.open_sftp)

        sftp_valid_ids: set[str] = set()

        async def _walk(path: str) -> int:
            count = 0
            try:
                entries = await asyncio.to_thread(sftp.listdir_attr, path)
            except Exception as exc:
                log.warning("SFTP listdir %s: %s", path, exc)
                return 0
            for entry in entries:
                full = f"{path}/{entry.filename}"
                if stat.S_ISDIR(entry.st_mode):
                    count += await _walk(full)
                elif _indexable(entry.filename):
                    try:
                        content = await asyncio.to_thread(lambda fp=full: sftp.open(fp, "rb").read())
                        n, ids = await ingest_file(qdrant, http, conn["id"], "sftp", full, content)
                        count += n
                        sftp_valid_ids.update(ids)
                        log.info("SFTP %s: %d chunks", full, n)
                    except Exception as exc:
                        log.warning("SFTP %s: %s", full, exc)
            return count

        for path in conn.get("paths", []):
            total += await _walk(path)
        await asyncio.to_thread(_delete_stale_points, qdrant, conn["id"], sftp_valid_ids)
        sftp.close()
    finally:
        ssh.close()
    return total


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> int:
    """Run all ingestion sources. Returns count of errors."""
    k8s    = K8s()
    qdrant = QdrantClient(url=QDRANT_URL)
    total  = 0
    errors = 0

    async with httpx.AsyncClient() as http:

        for src in k8s.read_json("rag-gdrive-config").get("sources", []):
            if not src.get("enabled", True):
                continue
            log.info("Ingesting GDrive: %s", src.get("name", src["id"]))
            try:
                total += await ingest_gdrive(qdrant, http, k8s, src)
            except Exception as exc:
                log.error("GDrive %s failed: %s", src["id"], exc)
                errors += 1

        for src in k8s.read_json("rag-s3-config").get("sources", []):
            if not src.get("enabled", True):
                continue
            log.info("Ingesting S3: %s", src.get("name", src["id"]))
            try:
                total += await ingest_s3(qdrant, http, k8s, src)
            except Exception as exc:
                log.error("S3 %s failed: %s", src["id"], exc)
                errors += 1

        for vol in k8s.read_json("rag-filesystem-config").get("volumes", []):
            if not vol.get("enabled", True):
                continue
            log.info("Ingesting filesystem: %s", vol.get("name", vol["id"]))
            try:
                total += await ingest_filesystem(qdrant, http, vol)
            except Exception as exc:
                log.error("FS %s failed: %s", vol["id"], exc)
                errors += 1

        for conn in k8s.read_json("rag-sftp-config").get("connections", []):
            if not conn.get("enabled", True):
                continue
            log.info("Ingesting SFTP: %s", conn.get("name", conn["id"]))
            try:
                total += await ingest_sftp(qdrant, http, k8s, conn)
            except Exception as exc:
                log.error("SFTP %s failed: %s", conn["id"], exc)
                errors += 1

    log.info("Ingestion complete — %d chunks upserted, %d source(s) failed", total, errors)
    return errors


if __name__ == "__main__":
    import sys
    error_count = asyncio.run(main())
    sys.exit(1 if error_count else 0)
