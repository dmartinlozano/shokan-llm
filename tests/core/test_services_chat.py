"""Unit tests for ChatService and AuditLog."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


# ── AuditLog ───────────────────────────────────────────────────────────────────

class TestAuditLog:
    @pytest.mark.asyncio
    async def test_log_writes_valid_json_to_stdout(self, capsys):
        from services.audit import AuditLog
        audit = AuditLog()
        await audit.log("user-123", "chat", "ollama/llama3", {"tokens": 42})
        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert event["user_id"] == "user-123"
        assert event["action"] == "chat"
        assert event["resource"] == "ollama/llama3"
        assert event["details"]["tokens"] == 42
        assert "ts" in event

    @pytest.mark.asyncio
    async def test_log_handles_none_details(self, capsys):
        from services.audit import AuditLog
        audit = AuditLog()
        await audit.log("u", "login", "keycloak")
        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert event["details"] == {}

    @pytest.mark.asyncio
    async def test_log_timestamp_is_numeric(self, capsys):
        from services.audit import AuditLog
        audit = AuditLog()
        await audit.log("u", "a", "r")
        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert isinstance(event["ts"], float)
        assert event["ts"] > 0


# ── ChatService tool collection ────────────────────────────────────────────────

class TestChatServiceToolCollection:
    def _make_service(self, fga_check_result=True, server_enabled=True):
        with patch("connectors.k8s.K8s") as MockK8s, \
             patch("connectors.openfga.OpenFGA") as MockFGA, \
             patch("connectors.rag.Retriever") as MockRetriever:
            from services.chat import ChatService

            svc = ChatService.__new__(ChatService)
            svc._k8s = MagicMock()
            svc._k8s.read.return_value = ""
            svc._k8s.read_json.return_value = (
                {"instances": [{"id": "inst1", "enabled": True}]} if server_enabled else {"instances": []}
            )
            svc._fga = MagicMock()
            svc._fga.check = AsyncMock(return_value=fga_check_result)
            svc._retriever = MagicMock()
            svc._retriever.retrieve = AsyncMock(return_value=[])
            svc._audit = MagicMock()
            svc._audit.log = AsyncMock()
            from connectors.mcp import MCP
            svc._mcp_cfg = MCP(svc._k8s, svc._fga)
            return svc

    @pytest.mark.asyncio
    async def test_no_tools_when_fga_denies(self):
        svc = self._make_service(fga_check_result=False)
        tools, client = await svc._collect_tools("user:alice")
        assert tools == []

    @pytest.mark.asyncio
    async def test_no_tools_when_server_disabled(self):
        svc = self._make_service(fga_check_result=True, server_enabled=False)
        tools, client = await svc._collect_tools("user:alice")
        assert tools == []

    @pytest.mark.asyncio
    async def test_tools_returned_when_allowed_and_enabled(self):
        svc = self._make_service(fga_check_result=True, server_enabled=True)
        tools, client = await svc._collect_tools("user:alice")
        assert len(tools) > 0

    @pytest.mark.asyncio
    async def test_tool_names_use_double_underscore(self):
        svc = self._make_service(fga_check_result=True, server_enabled=True)
        tools, _ = await svc._collect_tools("user:alice")
        for t in tools:
            name = t["function"]["name"]
            assert "__" in name, f"Tool name '{name}' missing __ separator"


# ── ChatService tool execution ─────────────────────────────────────────────────

class TestChatServiceRunTool:
    def _make_service(self):
        from services.chat import ChatService
        svc = ChatService.__new__(ChatService)
        svc._k8s = MagicMock()
        svc._k8s.read.return_value = ""
        svc._k8s.read_json.return_value = {}
        svc._fga = MagicMock()
        svc._retriever = MagicMock()
        svc._audit = MagicMock()
        svc._audit.log = AsyncMock()
        from connectors.mcp import MCP
        svc._mcp_cfg = MCP(svc._k8s, svc._fga)
        from connectors.mcp_client import MCPClient
        svc._mcp_client = MCPClient(svc._mcp_cfg, svc._k8s)
        return svc

    @pytest.mark.asyncio
    async def test_run_tool_with_invalid_json_args(self):
        svc = self._make_service()
        tool_call = {
            "id": "call_1",
            "function": {"name": "git__list_repos", "arguments": "not-json{{{"},
        }
        result = await svc._run_tool(tool_call, svc._mcp_client, "user:alice")
        # Should not raise; returns a string
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_run_tool_unknown_server_returns_error(self):
        svc = self._make_service()
        tool_call = {
            "id": "call_2",
            "function": {"name": "unknown__tool", "arguments": "{}"},
        }
        result = await svc._run_tool(tool_call, svc._mcp_client, "user:alice")
        assert "Unknown server type" in result

    @pytest.mark.asyncio
    async def test_run_tool_logs_audit_event(self):
        svc = self._make_service()
        tool_call = {
            "id": "call_3",
            "function": {"name": "git__list_repos", "arguments": "{}"},
        }
        await svc._run_tool(tool_call, svc._mcp_client, "user:alice")
        svc._audit.log.assert_called_once()
        call_args = svc._audit.log.call_args
        assert call_args[0][1] == "tool_call"  # action
        assert call_args[0][2] == "git__list_repos"  # resource


# ── ChatService streaming ──────────────────────────────────────────────────────

class TestChatServiceStreaming:
    def _make_service(self):
        from services.chat import ChatService
        svc = ChatService.__new__(ChatService)
        svc._k8s = MagicMock()
        svc._k8s.read.return_value = ""
        svc._k8s.read_json.return_value = {}
        svc._fga = MagicMock()
        svc._fga.check = AsyncMock(return_value=False)  # no MCP tools for user
        svc._retriever = MagicMock()
        svc._retriever.retrieve = AsyncMock(return_value=["chunk1", "chunk2"])
        svc._audit = MagicMock()
        svc._audit.log = AsyncMock()
        from connectors.mcp import MCP
        svc._mcp_cfg = MCP(svc._k8s, svc._fga)

        mock_skills = MagicMock()
        mock_skills.enabled_skills = MagicMock(return_value=[])
        self._skills_patch = patch("services.chat.SkillsStorage", return_value=mock_skills)
        self._skills_patch.start()
        return svc

    def teardown_method(self, _):
        if hasattr(self, "_skills_patch"):
            self._skills_patch.stop()

    def _make_sse_lines(self, tokens: list[str]) -> list[str]:
        lines = []
        for tok in tokens:
            chunk = {"choices": [{"delta": {"content": tok}}]}
            lines.append(f"data: {json.dumps(chunk)}")
        lines.append("data: [DONE]")
        return lines

    @pytest.mark.asyncio
    async def test_stream_response_no_tool_calls(self):
        """When LLM returns no tool calls, stream the final answer."""
        svc = self._make_service()

        # Non-streaming round returns no tool_calls
        no_tool_response = MagicMock()
        no_tool_response.is_success = True
        no_tool_response.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "hello", "tool_calls": []}}]
        }

        sse_lines = self._make_sse_lines(["Hello", ", ", "world", "!"])

        async def fake_aiter_lines():
            for line in sse_lines:
                yield line

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.aiter_lines = fake_aiter_lines

        mock_http_ctx = MagicMock()
        mock_http_ctx.__aenter__ = AsyncMock(return_value=mock_http_ctx)
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_http_ctx.post = AsyncMock(return_value=no_tool_response)
        mock_http_ctx.stream = MagicMock(return_value=mock_stream_ctx)

        with patch("httpx.AsyncClient", return_value=mock_http_ctx):
            gen = svc._run(
                user_id="user:alice",
                model="ollama/llama3",
                history=[{"role": "user", "content": "hi"}],
                litellm_url="http://litellm:8000",
                litellm_headers={},
                ollama_url="http://ollama:11434",
            )
            tokens = [t async for t in gen]

        assert tokens == ["Hello", ", ", "world", "!"]

    @pytest.mark.asyncio
    async def test_stream_response_with_rag_context(self):
        """RAG chunks are retrieved and injected into system prompt."""
        svc = self._make_service()
        captured_payloads: list[dict] = []

        no_tool_response = MagicMock()
        no_tool_response.is_success = True
        no_tool_response.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "answer"}}]
        }

        async def fake_aiter_lines():
            yield "data: [DONE]"

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_stream_ctx.aiter_lines = fake_aiter_lines

        async def capture_post(url, json=None, headers=None, timeout=None):
            captured_payloads.append(json or {})
            return no_tool_response

        mock_http_ctx = MagicMock()
        mock_http_ctx.__aenter__ = AsyncMock(return_value=mock_http_ctx)
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_http_ctx.post = AsyncMock(side_effect=capture_post)
        mock_http_ctx.stream = MagicMock(return_value=mock_stream_ctx)

        with patch("httpx.AsyncClient", return_value=mock_http_ctx):
            gen = svc._run(
                user_id="user:alice",
                model="ollama/llama3",
                history=[{"role": "user", "content": "what is chunk1?"}],
                litellm_url="http://litellm:8000",
                litellm_headers={},
                ollama_url="http://ollama:11434",
            )
            _ = [t async for t in gen]

        # The first LLM call must have a system message with RAG context
        assert captured_payloads, "No HTTP calls made"
        system_msg = captured_payloads[0]["messages"][0]
        assert system_msg["role"] == "system"
        assert "chunk1" in system_msg["content"]
        assert "chunk2" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_stream_response_http_error_yields_error_token(self):
        svc = self._make_service()

        error_response = MagicMock()
        error_response.is_success = False
        error_response.status_code = 503
        error_response.text = "Service Unavailable"

        mock_http_ctx = MagicMock()
        mock_http_ctx.__aenter__ = AsyncMock(return_value=mock_http_ctx)
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_http_ctx.post = AsyncMock(return_value=error_response)

        with patch("httpx.AsyncClient", return_value=mock_http_ctx):
            gen = svc._run(
                user_id="user:alice",
                model="ollama/llama3",
                history=[{"role": "user", "content": "hi"}],
                litellm_url="http://litellm:8000",
                litellm_headers={},
                ollama_url="http://ollama:11434",
            )
            tokens = [t async for t in gen]

        assert len(tokens) == 1
        assert "Error 503" in tokens[0]


# ── UIPermService ──────────────────────────────────────────────────────────────

class TestUIPermService:
    def setup_method(self):
        fga = MagicMock()
        fga.read_tuples_by_user = AsyncMock(return_value=[])
        fga.write = AsyncMock()
        from services.permissions import UIPermService
        self.svc = UIPermService(fga)

    @pytest.mark.asyncio
    async def test_admin_gets_all_permissions(self):
        from services.permissions import ALL_IDS
        perms = await self.svc.effective_for_user("uid", "admin")
        assert perms == ALL_IDS

    @pytest.mark.asyncio
    async def test_member_gets_limited_permissions(self):
        perms = await self.svc.effective_for_user("uid", "member")
        assert "chat:model:update" in perms
        assert "settings:users:create" not in perms

    @pytest.mark.asyncio
    async def test_unknown_role_returns_empty_set(self):
        perms = await self.svc.effective_for_user("uid", "ghost_role")
        assert perms == set()
