"""Chat cleanup job — deletes conversation .md files older than CHAT_RETENTION_DAYS.

Reads CHAT_DATA_DIR (default /data/chats) and CHAT_RETENTION_DAYS (default 30)
from environment variables. Designed to run as a K8s CronJob using the same
shokan-core image, with the chat PVC mounted at CHAT_DATA_DIR.
"""

import os
import re
import time
from pathlib import Path

_DATA_DIR       = Path(os.getenv("CHAT_DATA_DIR", "/data/chats"))
_RETENTION_DAYS = int(os.getenv("CHAT_RETENTION_DAYS", "30"))
_FRONT_MATTER   = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _read_ts(path: Path) -> float:
    """Return the ts float from the file's YAML front matter, or mtime as fallback."""
    try:
        text = path.read_text(encoding="utf-8")
        fm = _FRONT_MATTER.match(text)
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("ts: "):
                    return float(line[len("ts: "):])
    except Exception:
        pass
    return path.stat().st_mtime


def run() -> None:
    if not _DATA_DIR.exists():
        print(f"[chat-cleanup] {_DATA_DIR} does not exist — nothing to do.", flush=True)
        return

    cutoff  = time.time() - _RETENTION_DAYS * 86400
    deleted = 0
    errors  = 0

    for user_dir in _DATA_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        for md_file in user_dir.glob("*.md"):
            try:
                ts = _read_ts(md_file)
                if ts < cutoff:
                    md_file.unlink()
                    print(f"[chat-cleanup] deleted {md_file}", flush=True)
                    deleted += 1
            except Exception as exc:
                print(f"[chat-cleanup] error on {md_file}: {exc}", flush=True)
                errors += 1

    print(
        f"[chat-cleanup] done — {deleted} deleted, {errors} errors "
        f"(retention={_RETENTION_DAYS}d, cutoff={time.strftime('%Y-%m-%d', time.gmtime(cutoff))})",
        flush=True,
    )


if __name__ == "__main__":
    run()
