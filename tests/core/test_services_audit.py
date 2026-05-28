"""Unit tests for AuditLog in services/audit.py.

Covers file-based persistence, recent() filtering, and stats() aggregation.
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


def _make_audit(tmp_path: Path):
    """Return an AuditLog whose file is stored under tmp_path."""
    from services.audit import AuditLog

    audit_file = tmp_path / "audit.jsonl"
    audit = AuditLog()
    # Patch the module-level constants to use tmp_path
    import services.audit as _mod
    _mod._AUDIT_DIR = tmp_path
    _mod._AUDIT_FILE = audit_file
    return audit, audit_file


# ── recent() ──────────────────────────────────────────────────────────────────

def test_recent_returns_empty_when_file_missing(tmp_path: Path) -> None:
    audit, _ = _make_audit(tmp_path)
    assert audit.recent() == []


def test_recent_returns_events_newest_first(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1000.0, "user_id": "u1", "action": "chat", "resource": "gpt-4o", "details": {}},
        {"ts": 2000.0, "user_id": "u2", "action": "chat", "resource": "llama3", "details": {}},
        {"ts": 3000.0, "user_id": "u1", "action": "tool_call", "resource": "jira", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    result = audit.recent()
    assert len(result) == 3
    assert result[0]["ts"] == 3000.0  # newest first
    assert result[-1]["ts"] == 1000.0


def test_recent_filters_by_action(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1.0, "user_id": "u1", "action": "chat",      "resource": "gpt-4o", "details": {}},
        {"ts": 2.0, "user_id": "u2", "action": "tool_call", "resource": "jira",   "details": {}},
        {"ts": 3.0, "user_id": "u3", "action": "chat",      "resource": "llama3", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    result = audit.recent(action="chat")
    assert len(result) == 2
    assert all(e["action"] == "chat" for e in result)


def test_recent_filters_by_user_id(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1.0, "user_id": "alice", "action": "chat", "resource": "gpt-4o", "details": {}},
        {"ts": 2.0, "user_id": "bob",   "action": "chat", "resource": "llama3", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    result = audit.recent(user_id="alice")
    assert len(result) == 1
    assert result[0]["user_id"] == "alice"


def test_recent_respects_limit(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": float(i), "user_id": "u", "action": "chat", "resource": "m", "details": {}}
        for i in range(10)
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    result = audit.recent(limit=3)
    assert len(result) == 3
    assert result[0]["ts"] == 9.0  # last 3 reversed → newest first


def test_recent_skips_malformed_lines(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    with audit_file.open("w") as f:
        f.write('{"ts": 1.0, "user_id": "u", "action": "chat", "resource": "m", "details": {}}\n')
        f.write("not-json\n")
        f.write('{"ts": 2.0, "user_id": "u", "action": "chat", "resource": "m", "details": {}}\n')

    result = audit.recent()
    assert len(result) == 2


# ── stats() ───────────────────────────────────────────────────────────────────

def test_stats_returns_zeros_when_file_missing(tmp_path: Path) -> None:
    audit, _ = _make_audit(tmp_path)
    s = audit.stats()
    assert s["total"] == 0
    assert s["by_day"] == {}
    assert s["by_model"] == {}
    assert s["by_user"] == {}


def test_stats_counts_chat_events_only(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1_700_000_000.0, "user_id": "u1", "action": "chat",      "resource": "gpt-4o", "details": {}},
        {"ts": 1_700_000_060.0, "user_id": "u2", "action": "tool_call", "resource": "jira",   "details": {}},
        {"ts": 1_700_000_120.0, "user_id": "u1", "action": "chat",      "resource": "llama3", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    s = audit.stats()
    assert s["total"] == 2  # only chat events


def test_stats_groups_by_model(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1_700_000_000.0, "user_id": "u", "action": "chat", "resource": "gpt-4o", "details": {}},
        {"ts": 1_700_000_060.0, "user_id": "u", "action": "chat", "resource": "gpt-4o", "details": {}},
        {"ts": 1_700_000_120.0, "user_id": "u", "action": "chat", "resource": "llama3", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    s = audit.stats()
    assert s["by_model"]["gpt-4o"] == 2
    assert s["by_model"]["llama3"] == 1


def test_stats_groups_by_user(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    events = [
        {"ts": 1_700_000_000.0, "user_id": "alice", "action": "chat", "resource": "m", "details": {}},
        {"ts": 1_700_000_060.0, "user_id": "bob",   "action": "chat", "resource": "m", "details": {}},
        {"ts": 1_700_000_120.0, "user_id": "alice", "action": "chat", "resource": "m", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    s = audit.stats()
    assert s["by_user"]["alice"] == 2
    assert s["by_user"]["bob"] == 1


def test_stats_sorts_by_day_chronologically(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    import datetime
    # Write events across two different days
    day1 = datetime.datetime(2023, 11, 1, 12, 0, 0).timestamp()
    day2 = datetime.datetime(2023, 11, 3, 12, 0, 0).timestamp()
    day3 = datetime.datetime(2023, 11, 2, 12, 0, 0).timestamp()
    events = [
        {"ts": day1, "user_id": "u", "action": "chat", "resource": "m", "details": {}},
        {"ts": day2, "user_id": "u", "action": "chat", "resource": "m", "details": {}},
        {"ts": day3, "user_id": "u", "action": "chat", "resource": "m", "details": {}},
    ]
    with audit_file.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    s = audit.stats()
    days = list(s["by_day"].keys())
    assert days == sorted(days)


# ── log() persistence ──────────────────────────────────────────────────────────

async def test_log_appends_event_to_file(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    await audit.log("uid-1", "chat", "gpt-4o", {"query_len": 5})

    assert audit_file.exists()
    lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["user_id"] == "uid-1"
    assert ev["action"] == "chat"
    assert ev["resource"] == "gpt-4o"
    assert ev["details"]["query_len"] == 5


async def test_log_appends_multiple_events(tmp_path: Path) -> None:
    audit, audit_file = _make_audit(tmp_path)
    await audit.log("u1", "chat",      "gpt-4o", {})
    await audit.log("u2", "tool_call", "jira",   {})

    lines = [l for l in audit_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
