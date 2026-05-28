"""MCP tool execution — direct REST API calls per server type.

Each handler calls the external service API using credentials stored in K8s
secrets (via the MCP config connector). Tools are defined as OpenAI-compatible
function schemas so they can be passed directly to LiteLLM.
"""

import asyncio
import base64
import json
import re
import urllib.parse
from email.mime.text import MIMEText

import httpx

from connectors.k8s import K8s
from connectors.mcp import MCP

# ── Tool schemas ───────────────────────────────────────────────────────────────

_GIT_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "git__list_repos",
            "description": "List configured Git repositories with their IDs",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git__list_files",
            "description": "List files/directories in a Git repository path",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID (from git__list_repos)"},
                    "path": {"type": "string", "description": "Directory path relative to repo root (default: root)"},
                    "ref": {"type": "string", "description": "Branch, tag or commit SHA (default: HEAD)"},
                },
                "required": ["repo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git__read_file",
            "description": "Read the content of a file from a Git repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID (from git__list_repos)"},
                    "path": {"type": "string", "description": "File path relative to repo root"},
                    "ref": {"type": "string", "description": "Branch, tag or commit SHA (default: HEAD)"},
                },
                "required": ["repo_id", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git__recent_commits",
            "description": "Get recent commit history from a Git repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_id": {"type": "string", "description": "Repository ID (from git__list_repos)"},
                    "limit": {"type": "integer", "description": "Number of commits to return (default: 10)"},
                },
                "required": ["repo_id"],
            },
        },
    },
]

_JIRA_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "jira__search_issues",
            "description": "Search Jira issues using JQL",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Jira instance ID"},
                    "jql": {"type": "string", "description": "JQL query (e.g. 'project = PROJ AND status = Open')"},
                    "limit": {"type": "integer", "description": "Max results (default: 20)"},
                },
                "required": ["inst_id", "jql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira__get_issue",
            "description": "Get full details of a Jira issue by key",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Jira instance ID"},
                    "issue_key": {"type": "string", "description": "Issue key (e.g. PROJ-123)"},
                },
                "required": ["inst_id", "issue_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jira__create_issue",
            "description": "Create a new Jira issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Jira instance ID"},
                    "project_key": {"type": "string", "description": "Project key (e.g. PROJ)"},
                    "summary": {"type": "string", "description": "Issue title/summary"},
                    "description": {"type": "string", "description": "Issue description (optional)"},
                    "issue_type": {"type": "string", "description": "Issue type name (default: Task)"},
                },
                "required": ["inst_id", "project_key", "summary"],
            },
        },
    },
]

_CONFLUENCE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "confluence__search",
            "description": "Search Confluence pages by text",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Confluence instance ID"},
                    "query": {"type": "string", "description": "Search query text"},
                    "limit": {"type": "integer", "description": "Max results (default: 10)"},
                },
                "required": ["inst_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confluence__get_page",
            "description": "Get the content of a Confluence page by ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Confluence instance ID"},
                    "page_id": {"type": "string", "description": "Confluence page ID"},
                },
                "required": ["inst_id", "page_id"],
            },
        },
    },
]

_SLACK_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "slack__list_channels",
            "description": "List available Slack channels",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Slack instance ID"},
                },
                "required": ["inst_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slack__read_messages",
            "description": "Read recent messages from a Slack channel",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Slack instance ID"},
                    "channel": {"type": "string", "description": "Channel name or ID"},
                    "limit": {"type": "integer", "description": "Max messages (default: 20)"},
                },
                "required": ["inst_id", "channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "slack__send_message",
            "description": "Send a message to a Slack channel",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Slack instance ID"},
                    "channel": {"type": "string", "description": "Channel name or ID"},
                    "text": {"type": "string", "description": "Message text"},
                },
                "required": ["inst_id", "channel", "text"],
            },
        },
    },
]

_GMAIL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "gmail__list_emails",
            "description": "List recent emails from Gmail inbox",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Gmail instance ID"},
                    "query": {"type": "string", "description": "Gmail search query (e.g. 'is:unread from:boss@example.com')"},
                    "limit": {"type": "integer", "description": "Max emails (default: 10)"},
                },
                "required": ["inst_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gmail__send_email",
            "description": "Send an email via Gmail",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Gmail instance ID"},
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject"},
                    "body": {"type": "string", "description": "Email body (plain text)"},
                },
                "required": ["inst_id", "to", "subject", "body"],
            },
        },
    },
]

_DISCORD_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "discord__read_messages",
            "description": "Read recent messages from a Discord channel",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Discord instance ID"},
                    "channel_id": {"type": "string", "description": "Discord channel ID"},
                    "limit": {"type": "integer", "description": "Max messages (default: 20)"},
                },
                "required": ["inst_id", "channel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discord__send_message",
            "description": "Send a message to a Discord channel",
            "parameters": {
                "type": "object",
                "properties": {
                    "inst_id": {"type": "string", "description": "Discord instance ID"},
                    "channel_id": {"type": "string", "description": "Discord channel ID"},
                    "content": {"type": "string", "description": "Message content"},
                },
                "required": ["inst_id", "channel_id", "content"],
            },
        },
    },
]

SERVER_TOOLS: dict[str, list[dict]] = {
    "git": _GIT_TOOLS,
    "jira": _JIRA_TOOLS,
    "confluence": _CONFLUENCE_TOOLS,
    "slack": _SLACK_TOOLS,
    "gmail": _GMAIL_TOOLS,
    "discord": _DISCORD_TOOLS,
}


# ── MCPClient ──────────────────────────────────────────────────────────────────


class MCPClient:
    """Execute MCP tool calls by calling external service REST APIs directly."""

    def __init__(self, mcp: MCP, k8s: K8s) -> None:
        self._mcp = mcp
        self._k8s = k8s

    def get_tools_for_server(self, server_id: str) -> list[dict]:
        return SERVER_TOOLS.get(server_id, [])

    async def call(self, server_id: str, tool_name: str, args: dict) -> str:
        """Dispatch a tool call to the correct handler. Always returns a string."""
        handler = _HANDLERS.get(server_id)
        if not handler:
            return f"[MCP] Unknown server type: {server_id}"
        try:
            return await handler(self._mcp, self._k8s, tool_name, args)
        except Exception as exc:
            return f"[MCP error] {server_id}/{tool_name}: {exc}"


# ── Git handler ────────────────────────────────────────────────────────────────


async def _handle_git(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    cfg = await asyncio.to_thread(mcp.get_git_config)
    repos = cfg.get("repositories", [])

    if tool_name == "git__list_repos":
        if not repos:
            return "No Git repositories configured."
        return "\n".join(f"- {r['name']} (id: {r['id']}) — {r['url']}" for r in repos)

    repo_id = args.get("repo_id", "")
    repo = next((r for r in repos if r["id"] == repo_id), None)
    if not repo:
        return f"Repository '{repo_id}' not found. Use git__list_repos to see available repos."

    url = repo.get("url", "")
    token = await asyncio.to_thread(k8s.read, f"git-repo-token-{repo_id}") if repo.get("auth") == "token" else ""

    if "github.com" in url:
        return await _github(url, token, tool_name, args)
    if "gitlab" in url:
        return await _gitlab(url, token, tool_name, args)
    return f"[git] Unsupported provider for URL: {url}"


async def _github(repo_url: str, token: str, tool_name: str, args: dict) -> str:
    parts = repo_url.rstrip("/").split("github.com/")[-1].split("/")
    if len(parts) < 2:
        return f"[git] Cannot parse GitHub URL: {repo_url}"
    owner, repo = parts[0], parts[1].removesuffix(".git")
    hdrs = {"Accept": "application/vnd.github.v3+json"}
    if token:
        hdrs["Authorization"] = f"token {token}"

    async with httpx.AsyncClient() as http:
        if tool_name == "git__list_files":
            path = args.get("path", "")
            ref = args.get("ref", "HEAD")
            r = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
                headers=hdrs, params={"ref": ref}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitHub] {r.status_code}: {r.text[:200]}"
            items = r.json() if isinstance(r.json(), list) else [r.json()]
            return "\n".join(
                f"{'📁' if i['type'] == 'dir' else '📄'} {i['name']}" for i in items
            )

        if tool_name == "git__read_file":
            path = args.get("path", "")
            ref = args.get("ref", "HEAD")
            r = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
                headers=hdrs, params={"ref": ref}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitHub] {r.status_code}: {r.text[:200]}"
            content = base64.b64decode(r.json().get("content", "")).decode("utf-8", errors="replace")
            return content[:8000]

        if tool_name == "git__recent_commits":
            limit = args.get("limit", 10)
            r = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits",
                headers=hdrs, params={"per_page": limit}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitHub] {r.status_code}: {r.text[:200]}"
            lines = []
            for c in r.json():
                sha = (c.get("sha") or "")[:7]
                commit = c.get("commit") or {}
                msg = (commit.get("message") or "").split("\n")[0]
                author_info = commit.get("author") or {}
                author = author_info.get("name", "?")
                date = (author_info.get("date") or "")[:10]
                lines.append(f"{sha} {date} {author}: {msg}")
            return "\n".join(lines)

    return f"[git] Unknown tool: {tool_name}"


async def _gitlab(repo_url: str, token: str, tool_name: str, args: dict) -> str:
    parts = repo_url.split("://", 1)
    host_path = parts[-1].rstrip("/")
    slash_idx = host_path.find("/")
    if slash_idx == -1:
        return f"[git] Cannot parse GitLab URL: {repo_url}"
    host = host_path[:slash_idx]
    project_path = host_path[slash_idx + 1:].removesuffix(".git")
    encoded = urllib.parse.quote(project_path, safe="")
    base = f"https://{host}/api/v4"
    hdrs = {"PRIVATE-TOKEN": token}

    async with httpx.AsyncClient() as http:
        if tool_name == "git__list_files":
            path = args.get("path", "")
            ref = args.get("ref", "HEAD")
            r = await http.get(
                f"{base}/projects/{encoded}/repository/tree",
                headers=hdrs, params={"path": path, "ref": ref}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitLab] {r.status_code}: {r.text[:200]}"
            return "\n".join(
                f"{'📁' if i['type'] == 'tree' else '📄'} {i['name']}" for i in r.json()
            )

        if tool_name == "git__read_file":
            fpath = urllib.parse.quote(args.get("path", ""), safe="")
            ref = args.get("ref", "HEAD")
            r = await http.get(
                f"{base}/projects/{encoded}/repository/files/{fpath}/raw",
                headers=hdrs, params={"ref": ref}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitLab] {r.status_code}: {r.text[:200]}"
            return r.text[:8000]

        if tool_name == "git__recent_commits":
            limit = args.get("limit", 10)
            r = await http.get(
                f"{base}/projects/{encoded}/repository/commits",
                headers=hdrs, params={"per_page": limit}, timeout=10.0,
            )
            if not r.is_success:
                return f"[GitLab] {r.status_code}: {r.text[:200]}"
            lines = []
            for c in r.json():
                sha = (c.get("id") or "")[:7]
                date = (c.get("created_at") or "")[:10]
                lines.append(f"{sha} {date} {c.get('author_name', '?')}: {c.get('title', '')}")
            return "\n".join(lines)

    return f"[git] Unknown tool: {tool_name}"


# ── Jira handler ───────────────────────────────────────────────────────────────


async def _handle_jira(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    inst_id = args.get("inst_id", "")
    instances = await asyncio.to_thread(mcp.list_instances, "jira")
    inst = next((i for i in instances if i["id"] == inst_id), None) or (instances[0] if instances else None)
    if not inst:
        return "No Jira instances configured."
    inst_id = inst["id"]

    base = inst.get("url", "").rstrip("/")
    auth = (inst.get("email", ""), await asyncio.to_thread(mcp.get_instance_secret, "jira", inst_id, "token"))

    async with httpx.AsyncClient() as http:
        if tool_name == "jira__search_issues":
            jql = args.get("jql", "order by created DESC")
            limit = args.get("limit", 20)
            r = await http.get(
                f"{base}/rest/api/3/search",
                params={"jql": jql, "maxResults": limit, "fields": "summary,status,assignee,priority"},
                auth=auth, timeout=15.0,
            )
            if not r.is_success:
                return f"[Jira] {r.status_code}: {r.text[:300]}"
            issues = r.json().get("issues", [])
            if not issues:
                return "No issues found."
            return "\n".join(
                f"{i['key']}: {i['fields'].get('summary', '')} [{i['fields'].get('status', {}).get('name', '?')}]"
                for i in issues
            )

        if tool_name == "jira__get_issue":
            key = args.get("issue_key", "")
            r = await http.get(
                f"{base}/rest/api/3/issue/{key}",
                params={"fields": "summary,description,status,assignee,priority"},
                auth=auth, timeout=10.0,
            )
            if not r.is_success:
                return f"[Jira] {r.status_code}: {r.text[:300]}"
            d = r.json()
            f = d.get("fields", {})
            desc = _adf_to_text(f.get("description") or {})
            return (
                f"**{d['key']}: {f.get('summary', '')}**\n"
                f"Status: {f.get('status', {}).get('name', '?')} | "
                f"Assignee: {(f.get('assignee') or {}).get('displayName', 'Unassigned')} | "
                f"Priority: {(f.get('priority') or {}).get('name', '?')}\n\n"
                f"{desc[:2000]}"
            )

        if tool_name == "jira__create_issue":
            body: dict = {
                "fields": {
                    "project": {"key": args.get("project_key", "")},
                    "summary": args.get("summary", ""),
                    "issuetype": {"name": args.get("issue_type", "Task")},
                }
            }
            if args.get("description"):
                body["fields"]["description"] = {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [
                        {"type": "text", "text": args["description"]}
                    ]}],
                }
            r = await http.post(f"{base}/rest/api/3/issue", json=body, auth=auth, timeout=10.0)
            if not r.is_success:
                return f"[Jira] {r.status_code}: {r.text[:300]}"
            issue_key = r.json().get("key", "?")
            return f"Created issue {issue_key}: {base}/browse/{issue_key}"

    return f"[jira] Unknown tool: {tool_name}"


def _adf_to_text(node: dict, _depth: int = 0) -> str:
    """Recursively extract plain text from Jira ADF (Atlassian Document Format)."""
    if _depth > 10 or not node:
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    text = "".join(_adf_to_text(child, _depth + 1) for child in node.get("content", []))
    if node.get("type") in ("paragraph", "heading", "listItem"):
        text += "\n"
    return text


# ── Confluence handler ─────────────────────────────────────────────────────────


async def _handle_confluence(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    inst_id = args.get("inst_id", "")
    instances = await asyncio.to_thread(mcp.list_instances, "confluence")
    inst = next((i for i in instances if i["id"] == inst_id), None) or (instances[0] if instances else None)
    if not inst:
        return "No Confluence instances configured."
    inst_id = inst["id"]

    base = inst.get("url", "").rstrip("/")
    auth = (inst.get("email", ""), await asyncio.to_thread(mcp.get_instance_secret, "confluence", inst_id, "token"))

    async with httpx.AsyncClient() as http:
        if tool_name == "confluence__search":
            query = args.get("query", "")
            limit = args.get("limit", 10)
            r = await http.get(
                f"{base}/rest/api/content/search",
                params={"cql": f'type=page AND text~"{query}"', "limit": limit, "expand": "space"},
                auth=auth, timeout=15.0,
            )
            if not r.is_success:
                return f"[Confluence] {r.status_code}: {r.text[:300]}"
            results = r.json().get("results", [])
            if not results:
                return "No pages found."
            return "\n".join(
                f"[{p['id']}] {p.get('space', {}).get('name', '')}: {p['title']}"
                for p in results
            )

        if tool_name == "confluence__get_page":
            page_id = args.get("page_id", "")
            r = await http.get(
                f"{base}/rest/api/content/{page_id}",
                params={"expand": "body.storage"},
                auth=auth, timeout=10.0,
            )
            if not r.is_success:
                return f"[Confluence] {r.status_code}: {r.text[:300]}"
            d = r.json()
            raw_body = d.get("body", {}).get("storage", {}).get("value", "")
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_body)).strip()
            return f"**{d['title']}**\n\n{plain[:4000]}"

    return f"[confluence] Unknown tool: {tool_name}"


# ── Slack handler ──────────────────────────────────────────────────────────────


async def _handle_slack(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    inst_id = args.get("inst_id", "")
    instances = await asyncio.to_thread(mcp.list_instances, "slack")
    inst = next((i for i in instances if i["id"] == inst_id), None) or (instances[0] if instances else None)
    if not inst:
        return "No Slack instances configured."
    inst_id = inst["id"]

    token = await asyncio.to_thread(mcp.get_instance_secret, "slack", inst_id, "bot_token")
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as http:
        if tool_name == "slack__list_channels":
            r = await http.get(
                "https://slack.com/api/conversations.list",
                headers=hdrs, params={"limit": 100, "exclude_archived": "true"}, timeout=10.0,
            )
            data = r.json()
            if not data.get("ok"):
                return f"[Slack] {data.get('error', 'unknown error')}"
            return "\n".join(f"#{c['name']} (id: {c['id']})" for c in data.get("channels", []))

        if tool_name == "slack__read_messages":
            channel = args.get("channel", "")
            limit = args.get("limit", 20)
            r = await http.get(
                "https://slack.com/api/conversations.history",
                headers=hdrs, params={"channel": channel, "limit": limit}, timeout=10.0,
            )
            data = r.json()
            if not data.get("ok"):
                return f"[Slack] {data.get('error', 'unknown error')}"
            msgs = data.get("messages", [])
            if not msgs:
                return "No messages found."
            return "\n".join(
                f"{m.get('username', m.get('user', '?'))}: {m.get('text', '')}"
                for m in reversed(msgs)
            )

        if tool_name == "slack__send_message":
            r = await http.post(
                "https://slack.com/api/chat.postMessage",
                headers=hdrs,
                json={"channel": args.get("channel", ""), "text": args.get("text", "")},
                timeout=10.0,
            )
            data = r.json()
            if not data.get("ok"):
                return f"[Slack] {data.get('error', 'unknown error')}"
            return f"Message sent to {args.get('channel', '')}."

    return f"[slack] Unknown tool: {tool_name}"


# ── Gmail handler ──────────────────────────────────────────────────────────────


async def _handle_gmail(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    inst_id = args.get("inst_id", "")
    instances = await asyncio.to_thread(mcp.list_instances, "gmail")
    inst = next((i for i in instances if i["id"] == inst_id), None) or (instances[0] if instances else None)
    if not inst:
        return "No Gmail instances configured."
    inst_id = inst["id"]

    client_id = inst.get("client_id", "")
    client_secret = await asyncio.to_thread(mcp.get_instance_secret, "gmail", inst_id, "client_secret")
    refresh_token = await asyncio.to_thread(mcp.get_instance_secret, "gmail", inst_id, "refresh_token")
    access_token = await _gmail_refresh(client_id, client_secret, refresh_token)
    if not access_token:
        return "[Gmail] Could not obtain access token. Check credentials."

    hdrs = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as http:
        if tool_name == "gmail__list_emails":
            query = args.get("query", "")
            limit = args.get("limit", 10)
            r = await http.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=hdrs, params={"q": query, "maxResults": limit}, timeout=10.0,
            )
            if not r.is_success:
                return f"[Gmail] {r.status_code}: {r.text[:200]}"
            messages = r.json().get("messages", [])
            if not messages:
                return "No emails found."
            results = []
            for msg in messages[:limit]:
                detail = await http.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg['id']}",
                    headers=hdrs,
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                    timeout=10.0,
                )
                if detail.is_success:
                    h = {hd["name"]: hd["value"] for hd in detail.json().get("payload", {}).get("headers", [])}
                    results.append(f"From: {h.get('From', '?')} | {h.get('Subject', '?')} | {h.get('Date', '?')}")
            return "\n".join(results) or "No message details available."

        if tool_name == "gmail__send_email":
            msg = MIMEText(args.get("body", ""))
            msg["to"] = args.get("to", "")
            msg["subject"] = args.get("subject", "")
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            r = await http.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers=hdrs, json={"raw": raw}, timeout=10.0,
            )
            if not r.is_success:
                return f"[Gmail] {r.status_code}: {r.text[:200]}"
            return f"Email sent to {args.get('to', '?')}."

    return f"[gmail] Unknown tool: {tool_name}"


async def _gmail_refresh(client_id: str, client_secret: str, refresh_token: str) -> str:
    async with httpx.AsyncClient() as http:
        try:
            r = await http.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10.0,
            )
            if r.is_success:
                return r.json().get("access_token", "")
        except Exception:
            pass
    return ""


# ── Discord handler ────────────────────────────────────────────────────────────


async def _handle_discord(mcp: MCP, k8s: K8s, tool_name: str, args: dict) -> str:
    inst_id = args.get("inst_id", "")
    instances = await asyncio.to_thread(mcp.list_instances, "discord")
    inst = next((i for i in instances if i["id"] == inst_id), None) or (instances[0] if instances else None)
    if not inst:
        return "No Discord instances configured."
    inst_id = inst["id"]

    token = await asyncio.to_thread(mcp.get_instance_secret, "discord", inst_id, "bot_token")
    hdrs = {"Authorization": f"Bot {token}"}

    async with httpx.AsyncClient() as http:
        if tool_name == "discord__read_messages":
            channel_id = args.get("channel_id", "")
            limit = args.get("limit", 20)
            r = await http.get(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=hdrs, params={"limit": limit}, timeout=10.0,
            )
            if not r.is_success:
                return f"[Discord] {r.status_code}: {r.text[:200]}"
            msgs = r.json()
            if not msgs:
                return "No messages found."
            return "\n".join(
                f"{m.get('author', {}).get('username', '?')}: {m.get('content', '')}"
                for m in reversed(msgs)
            )

        if tool_name == "discord__send_message":
            channel_id = args.get("channel_id", "")
            r = await http.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=hdrs, json={"content": args.get("content", "")}, timeout=10.0,
            )
            if not r.is_success:
                return f"[Discord] {r.status_code}: {r.text[:200]}"
            return f"Message sent to channel {channel_id}."

    return f"[discord] Unknown tool: {tool_name}"


# ── Dispatch table ─────────────────────────────────────────────────────────────

_HANDLERS = {
    "git": _handle_git,
    "jira": _handle_jira,
    "confluence": _handle_confluence,
    "slack": _handle_slack,
    "gmail": _handle_gmail,
    "discord": _handle_discord,
}
