"""File-based chat storage — one Markdown file per conversation per user.

Layout: $CHAT_DATA_DIR/{user_id}/{chat_id}.md

File format (newest message at the top):

  ---
  title: <title>
  ts: <float>
  ---

  <!-- MSG:assistant -->
  <newest response>

  <!-- MSG:user -->
  <newest question>

  ...

  <!-- MSG:compacted -->
  <rolling summary — covers ALL messages that appear below this line>

  <!-- MSG:assistant -->
  <archived response — oldest in this batch>

  ...

  <!-- MSG:compacted -->
  <even older summary — the one this batch was built on>

  ...

Reading for LLM:  read from top until the first <!-- MSG:compacted -->, use it as context.
Reading for UI:   only show user/assistant messages above the first <!-- MSG:compacted -->.
Compaction:       when recent messages >= COMPACT_THRESHOLD, move the oldest COMPACT_BATCH
                  to archived and insert an updated compacted block.
"""

import os
import re
import time
from pathlib import Path

_DATA_DIR = Path(os.getenv("CHAT_DATA_DIR", "/data/chats"))
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_MSG_TAG_RE = re.compile(r"<!-- MSG:(user|assistant|system) -->")
_COMPACT_MARKER = "<!-- MSG:compacted -->"

COMPACT_THRESHOLD = 20   # trigger compaction when recent messages reach this count
COMPACT_BATCH     = 10   # oldest N messages moved to archived each compaction cycle


class ChatStorage:
    """Filesystem-backed chat store. All public methods are synchronous."""

    # ── Path helpers ───────────────────────────────────────────────────────────

    def _user_dir(self, user_id: str) -> Path:
        d = _DATA_DIR / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _chat_path(self, user_id: str, chat_id: str) -> Path:
        return self._user_dir(user_id) / f"{chat_id}.md"

    # ── Serialization ──────────────────────────────────────────────────────────

    @staticmethod
    def _encode(
        title: str,
        messages: list[dict],   # chronological (oldest first) → written newest-first
        ts: float,
        compact_summary: str = "",
        archived_raw: str = "",
    ) -> str:
        lines = ["---", f"title: {title}", f"ts: {ts}", "---", ""]

        for msg in reversed(messages):
            lines.append(f"<!-- MSG:{msg['role']} -->")
            lines.append(msg.get("content", ""))
            lines.append("")

        if compact_summary:
            lines.append(_COMPACT_MARKER)
            lines.append(compact_summary)
            lines.append("")
            if archived_raw:
                lines.append(archived_raw.rstrip())
                lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _decode(text: str) -> dict:
        title = "Chat"
        ts = 0.0

        fm = _FRONT_MATTER_RE.match(text)
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("title: "):
                    title = line[len("title: "):]
                elif line.startswith("ts: "):
                    try:
                        ts = float(line[len("ts: "):])
                    except ValueError:
                        pass
            text = text[fm.end():]

        compact_idx = text.find(_COMPACT_MARKER)
        if compact_idx == -1:
            recent_text = text
            compact_summary = ""
            archived_raw = ""
        else:
            recent_text = text[:compact_idx]
            after = text[compact_idx + len(_COMPACT_MARKER):]
            next_msg = after.find("<!-- MSG:")
            if next_msg == -1:
                compact_summary = after.strip()
                archived_raw = ""
            else:
                compact_summary = after[:next_msg].strip()
                archived_raw = after[next_msg:]

        # Parse recent user/assistant messages; file is newest-first → reverse to chronological
        messages: list[dict] = []
        parts = _MSG_TAG_RE.split(recent_text)
        it = iter(parts[1:])
        for role_str, content in zip(it, it):
            content = content.strip()
            if content:
                messages.append({"role": role_str, "content": content})
        messages.reverse()

        return {
            "title": title,
            "ts": ts,
            "messages": messages,            # chronological, recent uncompacted only
            "compact_summary": compact_summary,
            "archived_raw": archived_raw,
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def list_chats(self, user_id: str) -> list[dict]:
        """Return [{id, title, ts, message_count}] sorted newest-first."""
        d = _DATA_DIR / user_id
        if not d.exists():
            return []
        result = []
        for p in d.glob("*.md"):
            try:
                data = self._decode(p.read_text(encoding="utf-8"))
                result.append({
                    "id": p.stem,
                    "title": data["title"],
                    "ts": data["ts"],
                    "message_count": len(data["messages"]),
                })
            except Exception:
                continue
        result.sort(key=lambda x: -x["ts"])
        return result

    def load_chat(self, user_id: str, chat_id: str) -> dict | None:
        """Return {title, messages, ts, compact_summary, archived_raw} or None."""
        p = self._chat_path(user_id, chat_id)
        if not p.exists():
            return None
        try:
            return self._decode(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_chat(
        self,
        user_id: str,
        chat_id: str,
        title: str,
        messages: list[dict],   # chronological
        ts: float,
    ) -> None:
        """Write recent messages, preserving any existing compact sections."""
        existing = self.load_chat(user_id, chat_id)
        compact_summary = existing.get("compact_summary", "") if existing else ""
        archived_raw    = existing.get("archived_raw",    "") if existing else ""
        p = self._chat_path(user_id, chat_id)
        p.write_text(
            self._encode(title, messages, ts, compact_summary, archived_raw),
            encoding="utf-8",
        )

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        p = self._chat_path(user_id, chat_id)
        if p.exists():
            p.unlink()

    def clear_chat(self, user_id: str, chat_id: str) -> None:
        """Wipe all messages and compact history, keep the file."""
        data = self.load_chat(user_id, chat_id)
        if data:
            p = self._chat_path(user_id, chat_id)
            p.write_text(self._encode("New chat", [], data["ts"]), encoding="utf-8")

    # ── Compaction helpers ─────────────────────────────────────────────────────

    def needs_compaction(self, user_id: str, chat_id: str) -> bool:
        data = self.load_chat(user_id, chat_id)
        return bool(data and len(data["messages"]) >= COMPACT_THRESHOLD)

    def get_compaction_data(self, user_id: str, chat_id: str) -> tuple[list[dict], str] | None:
        """Return (oldest_batch, existing_summary) ready for the LLM summarizer, or None."""
        data = self.load_chat(user_id, chat_id)
        if not data or len(data["messages"]) < COMPACT_THRESHOLD:
            return None
        return data["messages"][:COMPACT_BATCH], data.get("compact_summary", "")

    def apply_compaction(self, user_id: str, chat_id: str, new_summary: str) -> list[dict]:
        """Move oldest COMPACT_BATCH messages to archived and insert a new compact marker.

        Returns the updated recent messages list so callers can sync in-memory state.
        """
        data = self.load_chat(user_id, chat_id)
        if not data or len(data["messages"]) < COMPACT_BATCH:
            return (data or {}).get("messages", [])

        msgs      = data["messages"]
        to_archive = msgs[:COMPACT_BATCH]    # oldest batch being archived
        remaining  = msgs[COMPACT_BATCH:]    # these stay as recent

        # Build the new archived_raw section:
        # newly archived messages (newest-first within the batch) + previous archived content
        archived_lines: list[str] = []
        for msg in reversed(to_archive):
            archived_lines.append(f"<!-- MSG:{msg['role']} -->")
            archived_lines.append(msg.get("content", ""))
            archived_lines.append("")
        old_raw = data.get("archived_raw", "").strip()
        if old_raw:
            archived_lines.append(old_raw)
            archived_lines.append("")
        new_archived_raw = "\n".join(archived_lines)

        p = self._chat_path(user_id, chat_id)
        p.write_text(
            self._encode(
                data["title"],
                remaining,
                data["ts"],
                compact_summary=new_summary,
                archived_raw=new_archived_raw,
            ),
            encoding="utf-8",
        )
        return remaining

    # ── Admin helpers ──────────────────────────────────────────────────────────

    def list_all_users(self) -> list[dict]:
        """Return [{user_id, chat_count, total_size_bytes}] for every user directory."""
        if not _DATA_DIR.exists():
            return []
        result = []
        for user_dir in _DATA_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            md_files = list(user_dir.glob("*.md"))
            total_size = sum(f.stat().st_size for f in md_files)
            result.append({
                "user_id": user_dir.name,
                "chat_count": len(md_files),
                "total_size_bytes": total_size,
            })
        result.sort(key=lambda x: x["user_id"])
        return result

    def delete_all_chats(self, user_id: str) -> int:
        """Delete every chat file for a user. Returns the number of files removed."""
        d = _DATA_DIR / user_id
        if not d.exists():
            return 0
        count = 0
        for p in d.glob("*.md"):
            p.unlink()
            count += 1
        return count

    # ── Legacy migration ───────────────────────────────────────────────────────

    def migrate_from_storage(self, user_id: str, chats: dict) -> None:
        """One-time import of chats from the old app.storage.user dict format."""
        for cid, chat in chats.items():
            if not self.load_chat(user_id, cid):
                self.save_chat(
                    user_id, cid,
                    chat.get("title", "Chat"),
                    chat.get("messages", []),
                    chat.get("ts", time.time()),
                )
