"""MCP server configuration and access management."""

import uuid

from connectors.k8s import K8s
from connectors.openfga import SHOKAN_OBJECT, OpenFGA

SERVERS = ["git", "jira", "confluence", "slack", "gmail", "discord"]

# ── Server metadata ────────────────────────────────────────────────────────────

SERVER_META: dict[str, dict] = {
    "git": {
        "icon": "code",
        "label": "Git",
        "description": "Git repositories (local or remote via HTTPS/SSH)",
        "columns": ["Name", "URL", "Auth"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Repo"},
            {"key": "url", "label": "URL", "placeholder": "https://github.com/org/repo"},
            {"key": "auth", "label": "Auth type", "type": "select", "options": ["none", "token", "ssh"]},
            {"key": "token", "label": "Token", "type": "password"},
        ],
    },
    "jira": {
        "icon": "bug_report",
        "label": "Jira",
        "description": "Project and issue management (Jira Cloud or Server)",
        "columns": ["Name", "URL", "Email"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Jira"},
            {"key": "url", "label": "URL", "placeholder": "https://yourorg.atlassian.net"},
            {"key": "email", "label": "Email", "placeholder": "admin@yourorg.com"},
            {"key": "project_keys", "label": "Projects (comma-separated)", "placeholder": "PROJ,DEV"},
            {"key": "token", "label": "API Token", "type": "password"},
        ],
        "secret_keys": ["token"],
    },
    "confluence": {
        "icon": "article",
        "label": "Confluence",
        "description": "Knowledge base (Confluence Cloud or Server)",
        "columns": ["Name", "URL", "Email"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Confluence"},
            {"key": "url", "label": "URL", "placeholder": "https://yourorg.atlassian.net/wiki"},
            {"key": "email", "label": "Email", "placeholder": "admin@yourorg.com"},
            {"key": "space_keys", "label": "Spaces (comma-separated)", "placeholder": "ENG,HR"},
            {"key": "token", "label": "API Token", "type": "password"},
        ],
        "secret_keys": ["token"],
    },
    "slack": {
        "icon": "chat",
        "label": "Slack",
        "description": "Team messaging — requires a Slack App with bot permissions",
        "columns": ["Name", "Channels"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Slack"},
            {"key": "channels", "label": "Allowed channels (comma-separated)", "placeholder": "#general,#dev"},
            {"key": "bot_token", "label": "Bot Token", "type": "password", "placeholder": "xoxb-…"},
            {"key": "signing_secret", "label": "Signing Secret", "type": "password"},
        ],
        "secret_keys": ["bot_token", "signing_secret"],
    },
    "gmail": {
        "icon": "email",
        "label": "Gmail",
        "description": "Email via OAuth 2.0",
        "columns": ["Name", "Client ID"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Gmail"},
            {"key": "client_id", "label": "OAuth Client ID"},
            {"key": "client_secret", "label": "OAuth Client Secret", "type": "password"},
            {"key": "refresh_token", "label": "Refresh Token", "type": "password"},
        ],
        "secret_keys": ["client_secret", "refresh_token"],
    },
    "discord": {
        "icon": "forum",
        "label": "Discord",
        "description": "Discord server channels via bot token",
        "columns": ["Name", "Servers"],
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "My Discord"},
            {"key": "servers", "label": "Server IDs (comma-separated)", "placeholder": "123456789,987654321"},
            {"key": "channels", "label": "Allowed channels (comma-separated, optional)", "placeholder": "general,dev"},
            {"key": "bot_token", "label": "Bot Token", "type": "password", "placeholder": "MTxx…"},
        ],
        "secret_keys": ["bot_token"],
    },
}


class MCP:
    """Manage MCP server configuration (K8s secret) and access control (OpenFGA).

    git-mcp-config schema: {"enabled": bool, "repositories": [{id, name, url, auth}]}
    Token for repo <id>: secret key git-repo-token-<id>
    """

    def __init__(self, k8s: K8s, fga: OpenFGA) -> None:
        self.k8s = k8s
        self.fga = fga

    # ── Git config ─────────────────────────────────────────────────────────────

    def get_git_config(self) -> dict:
        return self.k8s.read_json("git-mcp-config")

    def save_git_config(self, config: dict) -> None:
        self.k8s.write_json("git-mcp-config", config)

    def set_git_enabled(self, enabled: bool) -> None:
        cfg = self.get_git_config()
        cfg["enabled"] = enabled
        self.save_git_config(cfg)

    def add_git_repo(
        self,
        name: str,
        url: str,
        auth: str = "none",
        token: str = "",
    ) -> str:
        """Add a repository and return its generated id."""
        repo_id = uuid.uuid4().hex[:8]
        cfg = self.get_git_config()
        cfg.setdefault("repositories", []).append(
            {"id": repo_id, "name": name, "url": url, "auth": auth}
        )
        self.save_git_config(cfg)
        if token and auth == "token":
            self.k8s.write(f"git-repo-token-{repo_id}", token)
        return repo_id

    def remove_git_repo(self, repo_id: str) -> None:
        cfg = self.get_git_config()
        removed = next((r for r in cfg.get("repositories", []) if r.get("id") == repo_id), {})
        cfg["repositories"] = [
            r for r in cfg.get("repositories", []) if r.get("id") != repo_id
        ]
        self.save_git_config(cfg)
        if removed.get("auth") == "token":
            self.k8s.delete_key(f"git-repo-token-{repo_id}")

    # ── Generic server config ──────────────────────────────────────────────────

    def get_server_config(self, server_id: str) -> dict:
        """Return config dict for any MCP server (key: mcp-<id>-config)."""
        return self.k8s.read_json(f"mcp-{server_id}-config")

    def save_server_config(self, server_id: str, config: dict) -> None:
        self.k8s.write_json(f"mcp-{server_id}-config", config)

    def set_server_enabled(self, server_id: str, enabled: bool) -> None:
        cfg = self.get_server_config(server_id)
        cfg["enabled"] = enabled
        self.save_server_config(server_id, cfg)

    def get_server_secret(self, server_id: str, field: str) -> str:
        """Return a secret field stored as a separate K8s key (mcp-<id>-<field>)."""
        return self.k8s.read(f"mcp-{server_id}-{field}")

    def save_server_secret(self, server_id: str, field: str, value: str) -> None:
        self.k8s.write(f"mcp-{server_id}-{field}", value)

    # ── Multi-instance config (non-git servers) ───────────────────────────────

    def list_instances(self, server_id: str) -> list[dict]:
        """Return all named configurations for a connector (key: mcp-<id>-instances)."""
        return self.k8s.read_json(f"mcp-{server_id}-instances").get("instances", [])

    def add_instance(self, server_id: str, fields: dict, secrets: dict) -> str:
        """Persist a new named instance; store secrets as separate K8s keys."""
        inst_id = uuid.uuid4().hex[:8]
        cfg = self.k8s.read_json(f"mcp-{server_id}-instances")
        cfg.setdefault("instances", []).append({"id": inst_id, "enabled": True, **fields})
        self.k8s.write_json(f"mcp-{server_id}-instances", cfg)
        for key, val in secrets.items():
            if val:
                self.k8s.write(f"mcp-{server_id}-inst-{inst_id}-{key}", val)
        return inst_id

    def update_instance(self, server_id: str, inst_id: str, fields: dict, secrets: dict) -> None:
        cfg = self.k8s.read_json(f"mcp-{server_id}-instances")
        for inst in cfg.get("instances", []):
            if inst["id"] == inst_id:
                inst.update(fields)
        self.k8s.write_json(f"mcp-{server_id}-instances", cfg)
        for key, val in secrets.items():
            if val:
                self.k8s.write(f"mcp-{server_id}-inst-{inst_id}-{key}", val)

    def remove_instance(self, server_id: str, inst_id: str) -> None:
        cfg = self.k8s.read_json(f"mcp-{server_id}-instances")
        cfg["instances"] = [i for i in cfg.get("instances", []) if i["id"] != inst_id]
        self.k8s.write_json(f"mcp-{server_id}-instances", cfg)
        for key in SERVER_META.get(server_id, {}).get("secret_keys", []):
            self.k8s.delete_key(f"mcp-{server_id}-inst-{inst_id}-{key}")

    def get_instance_secret(self, server_id: str, inst_id: str, field: str) -> str:
        return self.k8s.read(f"mcp-{server_id}-inst-{inst_id}-{field}")

