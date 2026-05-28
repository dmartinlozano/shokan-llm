"""RAG data source configuration — K8s-backed CRUD for S3, GDrive, Filesystem, SFTP."""

import asyncio
import uuid

from connectors.k8s import K8s
from connectors.rag import RAG


class RagConfig:
    """CRUD operations for all RAG (cold data) source types stored in K8s."""

    def __init__(self, k8s: K8s, rag: RAG) -> None:
        self.k8s = k8s
        self.rag = rag

    # ── S3 ─────────────────────────────────────────────────────────────────────

    def list_s3_credentials(self) -> list[dict]:
        return self.k8s.read_json("rag-s3-credentials").get("credentials", [])

    def add_s3_credential(self, name: str, access_key_id: str, region: str, endpoint: str, secret_access_key: str) -> str:
        cid = uuid.uuid4().hex[:8]
        cfg = self.k8s.read_json("rag-s3-credentials")
        cfg.setdefault("credentials", []).append({
            "id": cid, "name": name, "access_key_id": access_key_id,
            "region": region, "endpoint": endpoint,
        })
        self.k8s.write_json("rag-s3-credentials", cfg)
        if secret_access_key:
            self.k8s.write(f"rag-s3-cred-{cid}-secret-key", secret_access_key)
        return cid

    def update_s3_credential(self, cid: str, name: str, access_key_id: str, region: str, endpoint: str, secret_access_key: str) -> None:
        cfg = self.k8s.read_json("rag-s3-credentials")
        for c in cfg.get("credentials", []):
            if c["id"] == cid:
                c.update({"name": name, "access_key_id": access_key_id, "region": region, "endpoint": endpoint})
        self.k8s.write_json("rag-s3-credentials", cfg)
        if secret_access_key:
            self.k8s.write(f"rag-s3-cred-{cid}-secret-key", secret_access_key)

    def delete_s3_credential(self, cid: str) -> None:
        cfg = self.k8s.read_json("rag-s3-credentials")
        cfg["credentials"] = [c for c in cfg.get("credentials", []) if c["id"] != cid]
        self.k8s.write_json("rag-s3-credentials", cfg)
        self.k8s.delete_key(f"rag-s3-cred-{cid}-secret-key")

    def list_s3_buckets(self) -> list[dict]:
        creds = self.list_s3_credentials()
        cred_map = {c["id"]: c["name"] for c in creds}
        sources = self.k8s.read_json("rag-s3-config").get("sources", [])
        return [
            {"name": s["name"], "bucket": s["bucket"],
             "credentials": cred_map.get(s.get("credential_id", ""), "—"),
             "_id": s["id"], "_credential_id": s.get("credential_id", "")}
            for s in sources
        ]

    async def add_s3_bucket(self, name: str, bucket: str, prefix: str, credential_id: str) -> str:
        sid = uuid.uuid4().hex[:8]
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-s3-config")
        cfg.setdefault("sources", []).append({
            "id": sid, "name": name, "bucket": bucket,
            "prefix": prefix, "enabled": True, "credential_id": credential_id,
        })
        await asyncio.to_thread(self.k8s.write_json, "rag-s3-config", cfg)
        await self.rag.add_datasource(sid, name or sid, "s3")
        return sid

    def update_s3_bucket(self, sid: str, name: str, bucket: str, prefix: str, credential_id: str) -> None:
        cfg = self.k8s.read_json("rag-s3-config")
        for s in cfg.get("sources", []):
            if s["id"] == sid:
                s.update({"name": name, "bucket": bucket, "prefix": prefix, "credential_id": credential_id})
        self.k8s.write_json("rag-s3-config", cfg)

    async def delete_s3_bucket(self, sid: str) -> None:
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-s3-config")
        cfg["sources"] = [s for s in cfg.get("sources", []) if s["id"] != sid]
        await asyncio.to_thread(self.k8s.write_json, "rag-s3-config", cfg)
        await self.rag.remove_datasource(sid)

    # ── Google Drive ───────────────────────────────────────────────────────────

    def list_gdrive_credentials(self) -> list[dict]:
        return self.k8s.read_json("rag-gdrive-credentials").get("credentials", [])

    def add_gdrive_credential(self, name: str, client_id: str, client_secret: str, refresh_token: str) -> str:
        cid = uuid.uuid4().hex[:8]
        cfg = self.k8s.read_json("rag-gdrive-credentials")
        cfg.setdefault("credentials", []).append({
            "id": cid, "name": name, "client_id": client_id,
            "has_client_secret": bool(client_secret), "has_refresh_token": bool(refresh_token),
        })
        self.k8s.write_json("rag-gdrive-credentials", cfg)
        if client_secret:
            self.k8s.write(f"rag-gdrive-cred-{cid}-client-secret", client_secret)
        if refresh_token:
            self.k8s.write(f"rag-gdrive-cred-{cid}-refresh-token", refresh_token)
        return cid

    def update_gdrive_credential(self, cid: str, name: str, client_id: str, client_secret: str, refresh_token: str) -> None:
        cfg = self.k8s.read_json("rag-gdrive-credentials")
        for c in cfg.get("credentials", []):
            if c["id"] == cid:
                c.update({
                    "name": name, "client_id": client_id,
                    "has_client_secret": bool(client_secret or c.get("has_client_secret")),
                    "has_refresh_token": bool(refresh_token or c.get("has_refresh_token")),
                })
        self.k8s.write_json("rag-gdrive-credentials", cfg)
        if client_secret:
            self.k8s.write(f"rag-gdrive-cred-{cid}-client-secret", client_secret)
        if refresh_token:
            self.k8s.write(f"rag-gdrive-cred-{cid}-refresh-token", refresh_token)

    def delete_gdrive_credential(self, cid: str) -> None:
        cfg = self.k8s.read_json("rag-gdrive-credentials")
        cfg["credentials"] = [c for c in cfg.get("credentials", []) if c["id"] != cid]
        self.k8s.write_json("rag-gdrive-credentials", cfg)
        self.k8s.delete_key(f"rag-gdrive-cred-{cid}-client-secret")
        self.k8s.delete_key(f"rag-gdrive-cred-{cid}-refresh-token")

    def list_gdrive_folders(self) -> list[dict]:
        creds = self.list_gdrive_credentials()
        cred_map = {c["id"]: c["name"] for c in creds}
        sources = self.k8s.read_json("rag-gdrive-config").get("sources", [])
        return [
            {
                "name": s["name"], "folder_id": s.get("folder_id", ""),
                "account": cred_map.get(s.get("credential_id", ""), "—"),
                "_id": s["id"], "_credential_id": s.get("credential_id", ""),
            }
            for s in sources
        ]

    async def add_gdrive_folder(self, name: str, folder_id: str, credential_id: str) -> str:
        sid = uuid.uuid4().hex[:8]
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-gdrive-config")
        cfg.setdefault("sources", []).append({
            "id": sid, "name": name, "folder_id": folder_id,
            "credential_id": credential_id, "enabled": True,
        })
        await asyncio.to_thread(self.k8s.write_json, "rag-gdrive-config", cfg)
        await self.rag.add_datasource(sid, name or sid, "gdrive")
        return sid

    def update_gdrive_folder(self, sid: str, name: str, folder_id: str, credential_id: str) -> None:
        cfg = self.k8s.read_json("rag-gdrive-config")
        for s in cfg.get("sources", []):
            if s["id"] == sid:
                s.update({"name": name, "folder_id": folder_id, "credential_id": credential_id})
        self.k8s.write_json("rag-gdrive-config", cfg)

    async def delete_gdrive_folder(self, sid: str) -> None:
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-gdrive-config")
        cfg["sources"] = [s for s in cfg.get("sources", []) if s["id"] != sid]
        await asyncio.to_thread(self.k8s.write_json, "rag-gdrive-config", cfg)
        await self.rag.remove_datasource(sid)

    # ── Filesystem ─────────────────────────────────────────────────────────────

    def list_volumes(self) -> list[dict]:
        try:
            pvcs = {p["name"]: p for p in self.k8s.list_pvcs("shokan-rag=true")}
        except Exception:
            pvcs = {}
        volumes = self.k8s.read_json("rag-filesystem-config").get("volumes", [])
        return [
            {
                "name": v["name"], "pvc": v.get("pvc_name", ""),
                "directory": v.get("scan_path", "/"),
                "status": pvcs.get(v.get("pvc_name", ""), {}).get("status", "not found"),
                "_id": v["id"], "_pvc_name": v.get("pvc_name", ""),
            }
            for v in volumes
        ]

    async def add_volume(self, name: str, directory: str, size_gb: int, storage_class: str) -> str:
        vid = uuid.uuid4().hex[:8]
        pvc_name = f"rag-vol-{vid}"
        await asyncio.to_thread(self.k8s.create_pvc, pvc_name, size_gb, storage_class, "ReadWriteOnce")
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-filesystem-config")
        cfg.setdefault("volumes", []).append({
            "id": vid, "name": name, "pvc_name": pvc_name,
            "scan_path": directory, "enabled": True,
        })
        await asyncio.to_thread(self.k8s.write_json, "rag-filesystem-config", cfg)
        await self.rag.add_datasource(vid, name or vid, "filesystem")
        return vid

    def update_volume(self, vid: str, name: str, directory: str) -> None:
        cfg = self.k8s.read_json("rag-filesystem-config")
        for v in cfg.get("volumes", []):
            if v["id"] == vid:
                v.update({"name": name, "scan_path": directory})
        self.k8s.write_json("rag-filesystem-config", cfg)

    async def delete_volume(self, vid: str, pvc_name: str) -> None:
        if pvc_name:
            await asyncio.to_thread(self.k8s.delete_pvc, pvc_name)
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-filesystem-config")
        cfg["volumes"] = [v for v in cfg.get("volumes", []) if v["id"] != vid]
        await asyncio.to_thread(self.k8s.write_json, "rag-filesystem-config", cfg)
        await self.rag.remove_datasource(vid)

    # ── SFTP ───────────────────────────────────────────────────────────────────

    def list_sftp_credentials(self) -> list[dict]:
        return self.k8s.read_json("rag-sftp-credentials").get("credentials", [])

    def add_sftp_credential(self, name: str, username: str, auth_type: str, password: str, private_key: str) -> str:
        cid = uuid.uuid4().hex[:8]
        cfg = self.k8s.read_json("rag-sftp-credentials")
        cfg.setdefault("credentials", []).append({
            "id": cid, "name": name, "username": username, "auth_type": auth_type,
            "has_password": bool(password), "has_key": bool(private_key),
        })
        self.k8s.write_json("rag-sftp-credentials", cfg)
        if password:
            self.k8s.write(f"rag-sftp-cred-{cid}-password", password)
        if private_key:
            self.k8s.write(f"rag-sftp-cred-{cid}-private-key", private_key)
        return cid

    def update_sftp_credential(self, cid: str, name: str, username: str, auth_type: str, password: str, private_key: str) -> None:
        cfg = self.k8s.read_json("rag-sftp-credentials")
        for c in cfg.get("credentials", []):
            if c["id"] == cid:
                c.update({
                    "name": name, "username": username, "auth_type": auth_type,
                    "has_password": bool(password or c.get("has_password")),
                    "has_key": bool(private_key or c.get("has_key")),
                })
        self.k8s.write_json("rag-sftp-credentials", cfg)
        if password:
            self.k8s.write(f"rag-sftp-cred-{cid}-password", password)
        if private_key:
            self.k8s.write(f"rag-sftp-cred-{cid}-private-key", private_key)

    def delete_sftp_credential(self, cid: str) -> None:
        cfg = self.k8s.read_json("rag-sftp-credentials")
        cfg["credentials"] = [c for c in cfg.get("credentials", []) if c["id"] != cid]
        self.k8s.write_json("rag-sftp-credentials", cfg)
        self.k8s.delete_key(f"rag-sftp-cred-{cid}-password")
        self.k8s.delete_key(f"rag-sftp-cred-{cid}-private-key")

    def list_sftp_connections(self) -> list[dict]:
        creds = self.list_sftp_credentials()
        cred_map = {c["id"]: c["name"] for c in creds}
        connections = self.k8s.read_json("rag-sftp-config").get("connections", [])
        return [
            {
                "name": c["name"],
                "host": f"{c.get('host', '—')}:{c.get('port', 22)}",
                "credentials": cred_map.get(c.get("credential_id", ""), "—"),
                "_id": c["id"],
                "_host": c.get("host", ""),
                "_port": c.get("port", 22),
                "_paths": c.get("paths", []),
                "_credential_id": c.get("credential_id", ""),
            }
            for c in connections
        ]

    async def add_sftp_connection(self, name: str, host: str, port: int, paths: list[str], credential_id: str) -> str:
        cid = uuid.uuid4().hex[:8]
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-sftp-config")
        cfg.setdefault("connections", []).append({
            "id": cid, "name": name, "host": host, "port": port,
            "paths": paths, "credential_id": credential_id, "enabled": True,
        })
        await asyncio.to_thread(self.k8s.write_json, "rag-sftp-config", cfg)
        await self.rag.add_datasource(cid, name or cid, "sftp")
        return cid

    def update_sftp_connection(self, cid: str, name: str, host: str, port: int, paths: list[str], credential_id: str) -> None:
        cfg = self.k8s.read_json("rag-sftp-config")
        for c in cfg.get("connections", []):
            if c["id"] == cid:
                c.update({"name": name, "host": host, "port": port, "paths": paths, "credential_id": credential_id})
        self.k8s.write_json("rag-sftp-config", cfg)

    async def delete_sftp_connection(self, cid: str) -> None:
        cfg = await asyncio.to_thread(self.k8s.read_json, "rag-sftp-config")
        cfg["connections"] = [c for c in cfg.get("connections", []) if c["id"] != cid]
        await asyncio.to_thread(self.k8s.write_json, "rag-sftp-config", cfg)
        await self.rag.remove_datasource(cid)
