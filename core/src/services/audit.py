"""Structured audit logging — writes JSON events to stdout + local file.

Each event is a single-line JSON object: {ts, user_id, action, resource, details}.
Kubernetes log collectors (Loki, Fluentd, CloudWatch) pick these up from stdout.
The local JSONL file enables the built-in audit log viewer and dashboard.
"""

import json
import os
import time
from collections import defaultdict
from pathlib import Path

_AUDIT_DIR = Path(os.getenv("AUDIT_DATA_DIR", "/data/audit"))
_AUDIT_FILE = _AUDIT_DIR / "audit.jsonl"


class AuditLog:
    """Write structured JSON audit events to stdout and local file."""

    async def log(
        self,
        user_id: str,
        action: str,
        resource: str,
        details: dict | None = None,
    ) -> None:
        event = {
            "ts": time.time(),
            "user_id": user_id,
            "action": action,
            "resource": resource,
            "details": details or {},
        }
        line = json.dumps(event, default=str)
        print(line, flush=True)
        try:
            _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            with _AUDIT_FILE.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def recent(
        self,
        limit: int = 200,
        user_id: str | None = None,
        action: str | None = None,
    ) -> list[dict]:
        """Return up to `limit` recent events (newest first), optionally filtered."""
        if not _AUDIT_FILE.exists():
            return []
        events: list[dict] = []
        try:
            with _AUDIT_FILE.open(encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        events.append(json.loads(raw))
                    except Exception:
                        continue
        except Exception:
            return []
        if user_id:
            events = [e for e in events if e.get("user_id") == user_id]
        if action:
            events = [e for e in events if e.get("action") == action]
        return list(reversed(events[-limit:]))

    def stats(self) -> dict:
        """Return aggregated stats from chat events: by_day, by_model, by_user, total."""
        if not _AUDIT_FILE.exists():
            return {"by_day": {}, "by_model": {}, "by_user": {}, "total": 0}
        import datetime
        by_day: dict = defaultdict(int)
        by_model: dict = defaultdict(int)
        by_user: dict = defaultdict(int)
        total = 0
        try:
            with _AUDIT_FILE.open(encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue
                    if ev.get("action") != "chat":
                        continue
                    total += 1
                    day = datetime.datetime.fromtimestamp(ev["ts"]).strftime("%Y-%m-%d")
                    by_day[day] += 1
                    by_model[ev.get("resource", "unknown")] += 1
                    by_user[ev.get("user_id", "unknown")] += 1
        except Exception:
            pass
        return {
            "by_day": dict(sorted(by_day.items())),
            "by_model": dict(sorted(by_model.items(), key=lambda x: -x[1])),
            "by_user": dict(sorted(by_user.items(), key=lambda x: -x[1])),
            "total": total,
        }
