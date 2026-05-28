"""Export and import service — full platform backup and restore."""

import asyncio
import datetime
import os
from pathlib import Path

from connectors.k8s import K8s
from connectors.openfga import OpenFGA
from services.chat_storage import ChatStorage
from services.skills import SkillsStorage

_CHAT_DIR = Path(os.getenv("CHAT_DATA_DIR", "/data/chats"))

FORMAT_VERSION = "1"

SECTIONS: dict[str, dict] = {
    "skills":      {"label": "Skills",           "icon": "auto_awesome",  "warning": None},
    "config":      {"label": "Platform Config",  "icon": "settings",      "warning": "Includes API keys and secrets."},
    "permissions": {"label": "Permissions",      "icon": "policy",        "warning": "Replaces ALL access rules. Users may lose access until next login."},
    "chats":       {"label": "Chat History",     "icon": "chat",          "warning": "Overwrites chat files. Large payload."},
}

_FGA_WRITE_CHUNK = 10


class ExportImportService:
    def __init__(self) -> None:
        self.k8s     = K8s()
        self.fga     = OpenFGA()
        self.skills  = SkillsStorage()
        self.storage = ChatStorage()

    # ── Export ─────────────────────────────────────────────────────────────────

    async def export(self, sections: list[str]) -> dict:
        """Build and return a JSON-serialisable export bundle."""
        bundle: dict = {
            "shokan_export_version": FORMAT_VERSION,
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "sections": {},
        }
        for section in sections:
            bundle["sections"][section] = await self._export_one(section)
        return bundle

    async def _export_one(self, section: str):
        if section == "skills":
            return await asyncio.to_thread(self._export_skills)
        if section == "config":
            return await asyncio.to_thread(self.k8s.read_all_keys)
        if section == "permissions":
            return await self.fga.read_all_tuples()
        if section == "chats":
            return await asyncio.to_thread(self._export_chats)
        return {}

    def _export_skills(self) -> list[dict]:
        result = []
        for meta in self.skills.list_skills():
            skill = self.skills.load_skill(meta["id"])
            if skill:
                result.append(skill)
        return result

    def _export_chats(self) -> dict:
        if not _CHAT_DIR.exists():
            return {}
        out: dict = {}
        for user_dir in _CHAT_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            uid = user_dir.name
            out[uid] = {}
            for p in user_dir.glob("*.md"):
                try:
                    out[uid][p.stem] = p.read_text(encoding="utf-8")
                except Exception:
                    pass
        return out

    # ── Import ─────────────────────────────────────────────────────────────────

    async def import_bundle(self, bundle: dict, sections: list[str] | None = None) -> dict:
        """Import sections from bundle. Returns {section: {ok, count, error}}."""
        results: dict = {}
        available = bundle.get("sections", {})
        to_import = sections if sections is not None else list(available.keys())
        for section in to_import:
            if section not in available:
                continue
            try:
                count = await self._import_one(section, available[section])
                results[section] = {"ok": True, "count": count}
            except Exception as exc:
                results[section] = {"ok": False, "error": str(exc)}
        return results

    async def _import_one(self, section: str, data) -> int:
        if section == "skills":
            return await asyncio.to_thread(self._import_skills, data)
        if section == "config":
            return await asyncio.to_thread(self._import_config, data)
        if section == "permissions":
            return await self._import_permissions(data)
        if section == "chats":
            return await asyncio.to_thread(self._import_chats, data)
        return 0

    def _import_skills(self, data: list) -> int:
        for meta in self.skills.list_skills():
            self.skills.delete_skill(meta["id"])
        for skill in data:
            sid = self.skills.create_skill(
                name=skill.get("name", "Imported"),
                content=skill.get("content", ""),
            )
            if not skill.get("enabled", True):
                self.skills.set_enabled(sid, False)
        return len(data)

    def _import_config(self, data: dict) -> int:
        for key, value in data.items():
            if isinstance(value, str):
                self.k8s.write(key, value)
        return len(data)

    async def _import_permissions(self, data: list) -> int:
        existing = await self.fga.read_all_tuples()
        # Delete existing in chunks
        for i in range(0, len(existing), _FGA_WRITE_CHUNK):
            chunk = existing[i:i + _FGA_WRITE_CHUNK]
            try:
                await self.fga.write(deletes=[
                    {"user": t["user"], "relation": t["relation"], "object": t["object"]}
                    for t in chunk
                ])
            except Exception:
                pass
        # Write new in chunks
        for i in range(0, len(data), _FGA_WRITE_CHUNK):
            chunk = data[i:i + _FGA_WRITE_CHUNK]
            await self.fga.write(writes=[
                {"user": t["user"], "relation": t["relation"], "object": t["object"]}
                for t in chunk
            ])
        return len(data)

    def _import_chats(self, data: dict) -> int:
        count = 0
        for uid, chats in data.items():
            user_dir = _CHAT_DIR / uid
            user_dir.mkdir(parents=True, exist_ok=True)
            for chat_id, content in chats.items():
                try:
                    (user_dir / f"{chat_id}.md").write_text(content, encoding="utf-8")
                    count += 1
                except Exception:
                    pass
        return count

    # ── Helpers ────────────────────────────────────────────────────────────────

    def section_summary(self, bundle: dict) -> dict[str, int]:
        """Return {section: count} for sections present in a bundle."""
        summary: dict = {}
        for section, data in bundle.get("sections", {}).items():
            if isinstance(data, list):
                summary[section] = len(data)
            elif isinstance(data, dict):
                summary[section] = len(data)
            else:
                summary[section] = 1
        return summary
