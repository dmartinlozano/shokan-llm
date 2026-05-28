"""Unit tests for ExportImportService in services/export_import.py.

All I/O dependencies (K8s, OpenFGA, SkillsStorage, ChatStorage) are mocked.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


def _make_svc():
    """Return an ExportImportService with all connectors mocked."""
    from services.export_import import ExportImportService

    svc = ExportImportService.__new__(ExportImportService)
    svc.k8s = MagicMock()
    svc.fga = MagicMock()
    svc.skills = MagicMock()
    svc.storage = MagicMock()
    return svc


# ── section_summary ────────────────────────────────────────────────────────────

def test_section_summary_list_section() -> None:
    from services.export_import import ExportImportService
    svc = _make_svc()
    bundle = {"shokan_export_version": "1", "sections": {"skills": [{"id": "a"}, {"id": "b"}]}}
    assert svc.section_summary(bundle) == {"skills": 2}


def test_section_summary_dict_section() -> None:
    svc = _make_svc()
    bundle = {"shokan_export_version": "1", "sections": {"config": {"KEY1": "v1", "KEY2": "v2"}}}
    assert svc.section_summary(bundle) == {"config": 2}


def test_section_summary_empty_sections() -> None:
    svc = _make_svc()
    bundle = {"shokan_export_version": "1", "sections": {}}
    assert svc.section_summary(bundle) == {}


def test_section_summary_mixed() -> None:
    svc = _make_svc()
    bundle = {
        "shokan_export_version": "1",
        "sections": {
            "skills": [1, 2, 3],
            "config": {"a": "1"},
            "permissions": [{"user": "u", "relation": "r", "object": "o"}],
        },
    }
    s = svc.section_summary(bundle)
    assert s["skills"] == 3
    assert s["config"] == 1
    assert s["permissions"] == 1


# ── export ─────────────────────────────────────────────────────────────────────

async def test_export_includes_requested_sections() -> None:
    svc = _make_svc()
    svc.skills.list_skills = MagicMock(return_value=[{"id": "s1"}])
    svc.skills.load_skill = MagicMock(return_value={"id": "s1", "name": "Skill1", "content": "x"})
    svc.k8s.read_all_keys = MagicMock(return_value={"API_KEY": "secret"})

    bundle = await svc.export(["skills", "config"])
    assert bundle["shokan_export_version"] == "1"
    assert "exported_at" in bundle
    assert "skills" in bundle["sections"]
    assert "config" in bundle["sections"]
    assert "permissions" not in bundle["sections"]


async def test_export_skills_returns_list() -> None:
    svc = _make_svc()
    svc.skills.list_skills = MagicMock(return_value=[{"id": "s1"}, {"id": "s2"}])
    svc.skills.load_skill = MagicMock(side_effect=lambda sid: {"id": sid, "name": sid, "content": ""})

    bundle = await svc.export(["skills"])
    assert isinstance(bundle["sections"]["skills"], list)
    assert len(bundle["sections"]["skills"]) == 2


async def test_export_config_returns_dict() -> None:
    svc = _make_svc()
    svc.k8s.read_all_keys = MagicMock(return_value={"K": "V"})

    bundle = await svc.export(["config"])
    assert bundle["sections"]["config"] == {"K": "V"}


async def test_export_permissions_calls_fga() -> None:
    svc = _make_svc()
    svc.fga.read_all_tuples = AsyncMock(return_value=[
        {"user": "user:alice", "relation": "allowed_user", "object": "ui_permission:chat:model:update"}
    ])

    bundle = await svc.export(["permissions"])
    svc.fga.read_all_tuples.assert_awaited_once()
    assert len(bundle["sections"]["permissions"]) == 1


async def test_export_chats(tmp_path: Path) -> None:
    svc = _make_svc()
    user_dir = tmp_path / "uid-1"
    user_dir.mkdir()
    (user_dir / "chat-abc.md").write_text("# Hello", encoding="utf-8")

    with patch("services.export_import._CHAT_DIR", tmp_path):
        bundle = await svc.export(["chats"])

    assert "uid-1" in bundle["sections"]["chats"]
    assert bundle["sections"]["chats"]["uid-1"]["chat-abc"] == "# Hello"


# ── import_bundle ──────────────────────────────────────────────────────────────

async def test_import_bundle_skills_replaces_existing() -> None:
    svc = _make_svc()
    svc.skills.list_skills = MagicMock(return_value=[{"id": "old-skill"}])
    svc.skills.delete_skill = MagicMock()
    svc.skills.create_skill = MagicMock(return_value="new-id")
    svc.skills.set_enabled = MagicMock()

    bundle = {
        "shokan_export_version": "1",
        "sections": {
            "skills": [{"name": "New Skill", "content": "do stuff", "enabled": True}]
        },
    }
    results = await svc.import_bundle(bundle, sections=["skills"])
    svc.skills.delete_skill.assert_called_once_with("old-skill")
    svc.skills.create_skill.assert_called_once()
    assert results["skills"]["ok"] is True
    assert results["skills"]["count"] == 1


async def test_import_bundle_skips_sections_not_in_bundle() -> None:
    svc = _make_svc()
    bundle = {"shokan_export_version": "1", "sections": {"skills": []}}
    results = await svc.import_bundle(bundle, sections=["skills", "config"])
    assert "config" not in results


async def test_import_bundle_config_writes_k8s() -> None:
    svc = _make_svc()
    svc.k8s.write = MagicMock()

    bundle = {"shokan_export_version": "1", "sections": {"config": {"KEY1": "val1", "KEY2": "val2"}}}
    results = await svc.import_bundle(bundle, sections=["config"])
    assert svc.k8s.write.call_count == 2
    assert results["config"]["count"] == 2


async def test_import_bundle_permissions_deletes_then_writes() -> None:
    svc = _make_svc()
    svc.fga.read_all_tuples = AsyncMock(return_value=[
        {"user": "u", "relation": "r", "object": "o"}
    ])
    svc.fga.write = AsyncMock()

    new_tuples = [{"user": "user:alice", "relation": "allowed_user", "object": "ui_permission:chat:model:update"}]
    bundle = {"shokan_export_version": "1", "sections": {"permissions": new_tuples}}
    results = await svc.import_bundle(bundle, sections=["permissions"])

    assert svc.fga.write.await_count >= 2  # at least one delete + one write
    assert results["permissions"]["count"] == 1


async def test_import_bundle_chats(tmp_path: Path) -> None:
    svc = _make_svc()

    bundle = {
        "shokan_export_version": "1",
        "sections": {
            "chats": {
                "uid-1": {"chat-xyz": "# My Chat"},
                "uid-2": {"chat-abc": "# Another Chat"},
            }
        },
    }
    with patch("services.export_import._CHAT_DIR", tmp_path):
        results = await svc.import_bundle(bundle, sections=["chats"])

    assert results["chats"]["count"] == 2
    assert (tmp_path / "uid-1" / "chat-xyz.md").read_text() == "# My Chat"
    assert (tmp_path / "uid-2" / "chat-abc.md").read_text() == "# Another Chat"


async def test_import_bundle_captures_section_error() -> None:
    svc = _make_svc()
    svc.k8s.write = MagicMock(side_effect=RuntimeError("k8s unreachable"))

    bundle = {"shokan_export_version": "1", "sections": {"config": {"KEY": "val"}}}
    results = await svc.import_bundle(bundle, sections=["config"])
    assert results["config"]["ok"] is False
    assert "k8s unreachable" in results["config"]["error"]


async def test_import_bundle_disabled_skill_sets_enabled_false() -> None:
    svc = _make_svc()
    svc.skills.list_skills = MagicMock(return_value=[])
    svc.skills.delete_skill = MagicMock()
    svc.skills.create_skill = MagicMock(return_value="new-id")
    svc.skills.set_enabled = MagicMock()

    bundle = {
        "shokan_export_version": "1",
        "sections": {
            "skills": [{"name": "Disabled Skill", "content": "x", "enabled": False}]
        },
    }
    await svc.import_bundle(bundle, sections=["skills"])
    svc.skills.set_enabled.assert_called_once_with("new-id", False)


async def test_export_empty_sections_list() -> None:
    svc = _make_svc()
    bundle = await svc.export([])
    assert bundle["sections"] == {}
