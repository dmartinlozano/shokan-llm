"""Unit tests for connector modules (MCP client, RAG retriever, K8s, OpenFGA)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "core" / "src"))


# ── MCPClient ──────────────────────────────────────────────────────────────────

class TestMCPClientToolSchemas:
    def setup_method(self):
        from connectors.mcp_client import MCPClient, SERVER_TOOLS
        self.SERVER_TOOLS = SERVER_TOOLS
        k8s = MagicMock()
        k8s.read.return_value = ""
        k8s.read_json.return_value = {}
        fga = MagicMock()
        from connectors.mcp import MCP
        mcp = MCP(k8s, fga)
        self.client = MCPClient(mcp, k8s)

    def test_all_server_types_have_tools(self):
        from connectors.mcp import SERVERS
        for server_id in SERVERS:
            tools = self.client.get_tools_for_server(server_id)
            assert tools, f"{server_id} has no tools defined"

    def test_tool_schemas_are_valid_openai_format(self):
        for server_id, tools in self.SERVER_TOOLS.items():
            for t in tools:
                assert t["type"] == "function", f"{server_id}: tool missing type=function"
                fn = t.get("function", {})
                assert "name" in fn, f"{server_id}: tool missing name"
                assert "description" in fn, f"{server_id}: tool missing description"
                assert "parameters" in fn, f"{server_id}: tool missing parameters"

    def test_tool_names_follow_server_prefix_convention(self):
        for server_id, tools in self.SERVER_TOOLS.items():
            for t in tools:
                name = t["function"]["name"]
                assert name.startswith(f"{server_id}__"), (
                    f"Tool '{name}' should start with '{server_id}__'"
                )

    def test_unknown_server_returns_empty_list(self):
        tools = self.client.get_tools_for_server("nonexistent")
        assert tools == []


class TestMCPClientDispatch:
    def setup_method(self):
        k8s = MagicMock()
        k8s.read.return_value = ""
        k8s.read_json.return_value = {}
        fga = MagicMock()
        from connectors.mcp import MCP
        from connectors.mcp_client import MCPClient
        mcp = MCP(k8s, fga)
        self.client = MCPClient(mcp, k8s)

    @pytest.mark.asyncio
    async def test_unknown_server_returns_error_string(self):
        result = await self.client.call("doesnotexist", "foo__bar", {})
        assert "Unknown server type" in result

    @pytest.mark.asyncio
    async def test_git_list_repos_no_config(self):
        result = await self.client.call("git", "git__list_repos", {})
        assert "No Git repositories configured" in result

    @pytest.mark.asyncio
    async def test_jira_no_instances(self):
        result = await self.client.call("jira", "jira__search_issues", {"inst_id": "x", "jql": "project = X"})
        assert "No Jira instances configured" in result

    @pytest.mark.asyncio
    async def test_slack_no_instances(self):
        result = await self.client.call("slack", "slack__list_channels", {"inst_id": "x"})
        assert "No Slack instances configured" in result

    @pytest.mark.asyncio
    async def test_discord_no_instances(self):
        result = await self.client.call("discord", "discord__read_messages", {"inst_id": "x", "channel_id": "123"})
        assert "No Discord instances configured" in result


class TestGitHandlerGitHub:
    def setup_method(self):
        k8s = MagicMock()
        k8s.read.return_value = "my-token"
        k8s.read_json.return_value = {
            "enabled": True,
            "repositories": [{"id": "abc123", "name": "my-repo", "url": "https://github.com/org/repo", "auth": "token"}],
        }
        fga = MagicMock()
        from connectors.mcp import MCP
        from connectors.mcp_client import MCPClient
        mcp = MCP(k8s, fga)
        self.client = MCPClient(mcp, k8s)

    @pytest.mark.asyncio
    async def test_list_repos(self):
        result = await self.client.call("git", "git__list_repos", {})
        assert "my-repo" in result
        assert "abc123" in result

    @pytest.mark.asyncio
    async def test_unknown_repo_id(self):
        result = await self.client.call("git", "git__list_files", {"repo_id": "zzz"})
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_read_file_calls_github_api(self):
        import base64
        import httpx

        fake_content = base64.b64encode(b"print('hello')").decode()
        mock_resp = MagicMock()
        mock_resp.is_success = True
        mock_resp.json.return_value = {"content": fake_content}

        with patch("httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await self.client.call("git", "git__read_file", {"repo_id": "abc123", "path": "main.py"})

        assert "print" in result


# ── K8s connector ──────────────────────────────────────────────────────────────

class TestK8sConnector:
    def setup_method(self):
        with patch("kubernetes.config.load_incluster_config"), \
             patch("kubernetes.config.load_kube_config"), \
             patch("kubernetes.client.CoreV1Api"):
            from connectors.k8s import K8s
            self.k8s = K8s()

    def test_read_json_returns_dict_on_empty(self):
        self.k8s.read = MagicMock(return_value="")
        assert self.k8s.read_json("missing-key") == {}

    def test_read_json_parses_valid_json(self):
        self.k8s.read = MagicMock(return_value='{"key": "value"}')
        result = self.k8s.read_json("some-key")
        assert result == {"key": "value"}

    def test_read_json_returns_empty_on_invalid_json(self):
        self.k8s.read = MagicMock(return_value="not-json{{{")
        assert self.k8s.read_json("bad-key") == {}


# ── OpenFGA connector ─────────────────────────────────────────────────────────

class TestOpenFGAConnector:
    def setup_method(self):
        from connectors.openfga import OpenFGA
        self.fga = OpenFGA()
        self.fga.store_id = ""  # dev mode: all checks return True

    @pytest.mark.asyncio
    async def test_check_returns_true_in_dev_mode(self):
        result = await self.fga.check("user:alice", "admin", "shokan:shokanllm")
        assert result is True

    @pytest.mark.asyncio
    async def test_write_noop_in_dev_mode(self):
        # Should not raise even with no store_id
        await self.fga.write(writes=[{"user": "user:x", "relation": "member", "object": "shokan:shokanllm"}])

    @pytest.mark.asyncio
    async def test_read_tuples_returns_empty_in_dev_mode(self):
        result = await self.fga.read_tuples("shokan:shokanllm")
        assert result == []


# ── ADF text extractor ─────────────────────────────────────────────────────────

class TestADFToText:
    def test_simple_paragraph(self):
        from connectors.mcp_client import _adf_to_text
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
        result = _adf_to_text(node)
        assert "Hello world" in result

    def test_empty_node_returns_empty_string(self):
        from connectors.mcp_client import _adf_to_text
        assert _adf_to_text({}) == ""

    def test_deeply_nested_does_not_recurse_infinitely(self):
        from connectors.mcp_client import _adf_to_text
        node: dict = {"type": "text", "text": "deep"}
        for _ in range(15):
            node = {"type": "paragraph", "content": [node]}
        result = _adf_to_text(node)
        assert isinstance(result, str)
