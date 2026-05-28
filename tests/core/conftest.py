"""Shared fixtures and mock setup for core unit tests."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make core/src importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))

# ── K8s mock fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_k8s():
    k8s = MagicMock()
    k8s.read.return_value = ""
    k8s.read_json.return_value = {}
    k8s.write.return_value = None
    k8s.write_json.return_value = None
    return k8s


# ── OpenFGA mock fixture ────────────────────────────────────────────────────────


@pytest.fixture
def mock_fga():
    fga = MagicMock()
    fga.check = AsyncMock(return_value=True)
    fga.write = AsyncMock(return_value=None)
    fga.read_tuples = AsyncMock(return_value=[])
    fga.get_object_tuples = AsyncMock(return_value={})
    fga.remove_relation = AsyncMock(return_value=None)
    return fga


# ── HTTP mock helpers ──────────────────────────────────────────────────────────


def make_http_response(status: int = 200, json_data: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp
