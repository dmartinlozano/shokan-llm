"""Filesystem-backed skills store — one Markdown file per skill.

Layout: $SKILLS_DATA_DIR/{skill_id}.md

File format:
  ---
  name: <name>
  enabled: true|false
  created_at: <float>
  ---

  <markdown content>

Skills are injected as additional system prompt context on every LLM call.
"""

import os
import re
import time
import uuid
from pathlib import Path

_DATA_DIR = Path(os.getenv("SKILLS_DATA_DIR", "/data/skills"))
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


class SkillsStorage:
    """Filesystem-backed skills store. All public methods are synchronous."""

    def _dir(self) -> Path:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        return _DATA_DIR

    def _path(self, skill_id: str) -> Path:
        return self._dir() / f"{skill_id}.md"

    @staticmethod
    def _encode(name: str, content: str, enabled: bool, created_at: float) -> str:
        return (
            f"---\nname: {name}\nenabled: {'true' if enabled else 'false'}\n"
            f"created_at: {created_at}\n---\n\n{content}"
        )

    @staticmethod
    def _decode(text: str, skill_id: str) -> dict:
        name = skill_id
        enabled = True
        created_at = 0.0

        fm = _FRONT_MATTER_RE.match(text)
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("name: "):
                    name = line[len("name: "):]
                elif line.startswith("enabled: "):
                    enabled = line[len("enabled: "):].strip().lower() == "true"
                elif line.startswith("created_at: "):
                    try:
                        created_at = float(line[len("created_at: "):])
                    except ValueError:
                        pass
            content = text[fm.end():].strip()
        else:
            content = text.strip()

        return {
            "id":         skill_id,
            "name":       name,
            "enabled":    enabled,
            "created_at": created_at,
            "content":    content,
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        """Return [{id, name, enabled, created_at}] sorted alphabetically by name."""
        result = []
        for p in self._dir().glob("*.md"):
            try:
                data = self._decode(p.read_text(encoding="utf-8"), p.stem)
                result.append({k: data[k] for k in ("id", "name", "enabled", "created_at")})
            except Exception:
                continue
        result.sort(key=lambda x: x["name"].lower())
        return result

    def load_skill(self, skill_id: str) -> dict | None:
        """Return {id, name, enabled, created_at, content} or None."""
        p = self._path(skill_id)
        if not p.exists():
            return None
        try:
            return self._decode(p.read_text(encoding="utf-8"), skill_id)
        except Exception:
            return None

    def save_skill(self, skill_id: str, name: str, content: str, enabled: bool = True) -> None:
        existing    = self.load_skill(skill_id)
        created_at  = existing["created_at"] if existing else time.time()
        self._path(skill_id).write_text(
            self._encode(name, content, enabled, created_at),
            encoding="utf-8",
        )

    def create_skill(self, name: str = "New skill", content: str = "") -> str:
        """Create a new skill and return its generated ID."""
        skill_id = str(uuid.uuid4())
        self.save_skill(skill_id, name, content, enabled=True)
        return skill_id

    def delete_skill(self, skill_id: str) -> None:
        p = self._path(skill_id)
        if p.exists():
            p.unlink()

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        skill = self.load_skill(skill_id)
        if skill:
            self.save_skill(skill_id, skill["name"], skill["content"], enabled)

    def enabled_skills(self) -> list[dict]:
        """Return [{name, content}] for all enabled skills, sorted by name."""
        result = []
        for p in sorted(self._dir().glob("*.md"), key=lambda x: x.name.lower()):
            try:
                data = self._decode(p.read_text(encoding="utf-8"), p.stem)
                if data.get("enabled") and data.get("content"):
                    result.append({"name": data["name"], "content": data["content"]})
            except Exception:
                continue
        return result
