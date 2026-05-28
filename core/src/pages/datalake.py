"""
Shokan-LLM Data Lake settings — page class.

Merges RAG cold sources and MCP live connectors into a single tabbed UI.
Imported by main.py and rendered at /datalake-settings.
No entrypoint, no auth routes, no standalone startup.
Requires a NiceGUI @ui.page context to call render().
"""

import asyncio

from nicegui import ui

from connectors.k8s import K8s
from connectors.mcp import MCP, SERVERS, SERVER_META
from connectors.openfga import SHOKAN_OBJECT, OpenFGA
from services.litellm_config import LiteLLMConfig
from services.rag_config import RagConfig
from connectors.rag import RAG
from services.permissions import can
from templates.crud_template import CRUDTemplate


# ══════════════════════════════════════════════════════════════════════════════
# RagView
# ══════════════════════════════════════════════════════════════════════════════


class RagView:
    """Renders the RAG data sources configuration page.

    Tabs: S3 · Google Drive · Filesystem · SFTP
    """

    def __init__(self) -> None:
        k8s = K8s()
        fga = OpenFGA()
        rag = RAG(k8s, fga)
        self.rag_cfg = RagConfig(k8s, rag)
        self.k8s = k8s

    # ══════════════════════════════════════════════════════════════════════════
    # Amazon S3
    # ══════════════════════════════════════════════════════════════════════════

    async def _render_s3_tab(self) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        ui.label("Amazon S3").classes("text-base font-semibold mb-1")
        ui.label("Manage AWS credential sets and the S3 buckets associated with each one.").classes("text-xs text-gray-500 mb-3")

        # ── Credentials ───────────────────────────────────────────────────────
        async def refresh_creds():
            return [
                {"name": c["name"], "access_key_id": c.get("access_key_id", ""), "region": c.get("region", ""), "_id": c["id"]}
                for c in await asyncio.to_thread(self.rag_cfg.list_s3_credentials)
            ]

        async def on_new_cred(data: dict):
            await asyncio.to_thread(
                self.rag_cfg.add_s3_credential,
                data.get("name", ""), data.get("access_key_id", ""),
                data.get("region", "us-east-1"), data.get("endpoint", ""),
                data.get("secret_access_key", ""),
            )
            ui.notify("Credentials added", type="positive")

        async def on_edit_cred(original: dict, data: dict):
            await asyncio.to_thread(
                self.rag_cfg.update_s3_credential,
                original["_id"], data.get("name", ""), data.get("access_key_id", ""),
                data.get("region", ""), data.get("endpoint", ""), data.get("secret_access_key", ""),
            )
            ui.notify("Credentials updated", type="positive")

        async def on_delete_cred(item: dict):
            await asyncio.to_thread(self.rag_cfg.delete_s3_credential, item["_id"])
            ui.notify("Credentials deleted", type="info")

        CRUDTemplate(
            title="S3 Credentials",
            columns=["Name", "Access Key ID", "Region"],
            on_refresh=refresh_creds,
            on_new=on_new_cred,
            on_edit=on_edit_cred,
            on_delete=on_delete_cred,
            fields=[
                {"key": "name", "label": "Name", "placeholder": "AWS Production"},
                {"key": "access_key_id", "label": "Access Key ID", "placeholder": "AKIA…"},
                {"key": "region", "label": "Default region", "placeholder": "us-east-1"},
                {"key": "endpoint", "label": "Endpoint (S3-compatible, optional)", "placeholder": "https://minio:9000"},
                {"key": "secret_access_key", "label": "Secret Access Key", "type": "password"},
            ],
        )

        ui.separator().classes("my-4")

        # ── Buckets ───────────────────────────────────────────────────────────
        buckets_crud: list = []

        async def _open_bucket_modal(item: dict | None = None) -> None:
            cred_opts = {c["id"]: c["name"] for c in await asyncio.to_thread(self.rag_cfg.list_s3_credentials)}
            is_edit = item is not None
            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[500px] p-6 gap-4"):
                    ui.label("Edit bucket" if is_edit else "New bucket").classes("text-xl font-bold text-slate-800")
                    with ui.column().classes("w-full gap-3"):
                        name_inp = ui.input(label="Name", value=item.get("name", "") if is_edit else "", placeholder="sales-docs").classes("w-full").props("outlined dense")
                        bucket_inp = ui.input(label="Bucket name", value=item.get("bucket", "") if is_edit else "", placeholder="my-company-bucket").classes("w-full").props("outlined dense")
                        prefix_inp = ui.input(label="Prefix (optional)", value=item.get("prefix", "") if is_edit else "", placeholder="data/").classes("w-full").props("outlined dense")
                        cred_keys = list(cred_opts.keys())
                        default_cred = (item.get("_credential_id", "") if is_edit else "") or (cred_keys[0] if cred_keys else "")
                        cred_sel = ui.select(cred_opts, value=default_cred, label="Credentials").classes("w-full").props("outlined dense")
                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")
                        async def _save(d=dlg):
                            try:
                                if is_edit:
                                    await asyncio.to_thread(self.rag_cfg.update_s3_bucket, item["_id"], name_inp.value, bucket_inp.value, prefix_inp.value, cred_sel.value)
                                    ui.notify("Bucket updated", type="positive")
                                else:
                                    await self.rag_cfg.add_s3_bucket(name_inp.value, bucket_inp.value, prefix_inp.value, cred_sel.value)
                                    ui.notify("Bucket added", type="positive")
                                d.close()
                                if buckets_crud:
                                    buckets_crud[0].refresh()
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")
                        ui.button("Save", on_click=_save).props("unelevated color=primary")
            dlg.open()

        async def refresh_buckets():
            return await asyncio.to_thread(self.rag_cfg.list_s3_buckets)

        async def on_delete_bucket(item: dict):
            await self.rag_cfg.delete_s3_bucket(item["_id"])
            ui.notify("Bucket removed", type="info")

        async def _new_bucket():
            await _open_bucket_modal()

        tpl = CRUDTemplate(
            title="S3 Buckets",
            columns=["Name", "Bucket", "Credentials"],
            on_refresh=refresh_buckets,
            on_new_click=_new_bucket,
            on_edit=_open_bucket_modal,
            on_delete=on_delete_bucket,
            direct_edit=True,
        )
        buckets_crud.append(tpl)

    # ══════════════════════════════════════════════════════════════════════════
    # Google Drive
    # ══════════════════════════════════════════════════════════════════════════

    async def _render_gdrive_tab(self) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        ui.label("Google Drive").classes("text-base font-semibold mb-1")
        ui.label("Multiple OAuth accounts. Each folder is linked to one account.").classes("text-xs text-gray-500 mb-3")

        # ── OAuth accounts ────────────────────────────────────────────────────
        async def refresh_accounts():
            return [
                {"name": c["name"], "client_id": c.get("client_id", ""), "_id": c["id"]}
                for c in await asyncio.to_thread(self.rag_cfg.list_gdrive_credentials)
            ]

        async def on_new_account(data: dict):
            await asyncio.to_thread(
                self.rag_cfg.add_gdrive_credential,
                data.get("name", ""), data.get("client_id", ""),
                data.get("client_secret", ""), data.get("refresh_token", ""),
            )
            ui.notify("OAuth account added", type="positive")

        async def on_edit_account(original: dict, data: dict):
            await asyncio.to_thread(
                self.rag_cfg.update_gdrive_credential,
                original["_id"], data.get("name", ""), data.get("client_id", ""),
                data.get("client_secret", ""), data.get("refresh_token", ""),
            )
            ui.notify("OAuth account updated", type="positive")

        async def on_delete_account(item: dict):
            await asyncio.to_thread(self.rag_cfg.delete_gdrive_credential, item["_id"])
            ui.notify("OAuth account removed", type="info")

        CRUDTemplate(
            title="Google OAuth Accounts",
            columns=["Name", "Client ID"],
            on_refresh=refresh_accounts,
            on_new=on_new_account,
            on_edit=on_edit_account,
            on_delete=on_delete_account,
            fields=[
                {"key": "name", "label": "Name", "placeholder": "Marketing account"},
                {"key": "client_id", "label": "Client ID", "placeholder": "xxx.apps.googleusercontent.com"},
                {"key": "client_secret", "label": "Client Secret", "type": "password"},
                {"key": "refresh_token", "label": "Refresh Token", "type": "password"},
            ],
        )

        ui.separator().classes("my-4")

        # ── Folders ───────────────────────────────────────────────────────────
        folders_crud: list = []

        async def _open_folder_modal(item: dict | None = None) -> None:
            account_opts = {c["id"]: c["name"] for c in await asyncio.to_thread(self.rag_cfg.list_gdrive_credentials)}
            is_edit = item is not None
            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[500px] p-6 gap-4"):
                    ui.label("Edit folder" if is_edit else "New folder").classes("text-xl font-bold text-slate-800")
                    with ui.column().classes("w-full gap-3"):
                        name_inp = ui.input(label="Name", value=item.get("name", "") if is_edit else "", placeholder="Marketing Docs").classes("w-full").props("outlined dense")
                        folder_inp = ui.input(label="Folder ID", value=item.get("folder_id", "") if is_edit else "", placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs or 'root'").classes("w-full").props("outlined dense")
                        acct_keys = list(account_opts.keys())
                        if not acct_keys:
                            ui.label("⚠ No OAuth accounts configured. Add one above first.").classes("text-sm text-amber-600")
                            acct_sel = None
                        else:
                            default_acct = (item.get("_credential_id", "") if is_edit else "") or acct_keys[0]
                            acct_sel = ui.select(account_opts, value=default_acct, label="OAuth Account").classes("w-full").props("outlined dense")
                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")
                        async def _save(d=dlg):
                            if acct_sel is None:
                                ui.notify("Add an OAuth account first.", type="warning")
                                return
                            try:
                                if is_edit:
                                    await asyncio.to_thread(self.rag_cfg.update_gdrive_folder, item["_id"], name_inp.value, folder_inp.value, acct_sel.value)
                                    ui.notify("Folder updated", type="positive")
                                else:
                                    await self.rag_cfg.add_gdrive_folder(name_inp.value, folder_inp.value, acct_sel.value)
                                    ui.notify("Folder added", type="positive")
                                d.close()
                                if folders_crud:
                                    folders_crud[0].refresh()
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")
                        ui.button("Save", on_click=_save).props("unelevated color=primary")
            dlg.open()

        async def refresh_folders():
            return await asyncio.to_thread(self.rag_cfg.list_gdrive_folders)

        async def on_delete_folder(item: dict):
            await self.rag_cfg.delete_gdrive_folder(item["_id"])
            ui.notify("Folder removed", type="info")

        async def _new_folder():
            await _open_folder_modal()

        tpl = CRUDTemplate(
            title="Google Drive Folders",
            columns=["Name", "Folder ID", "Account"],
            on_refresh=refresh_folders,
            on_new_click=_new_folder,
            on_edit=_open_folder_modal,
            on_delete=on_delete_folder,
            direct_edit=True,
        )
        folders_crud.append(tpl)

    # ══════════════════════════════════════════════════════════════════════════
    # Filesystem
    # ══════════════════════════════════════════════════════════════════════════

    async def _render_filesystem_tab(self) -> None:
        ui.label("Filesystem — Cluster volumes").classes("text-base font-semibold mb-1")
        ui.label("Each volume is a Kubernetes PVC managed automatically.").classes("text-xs text-gray-500 mb-3")

        storage_classes = await asyncio.to_thread(self.k8s.list_storage_classes)

        async def refresh_volumes():
            return await asyncio.to_thread(self.rag_cfg.list_volumes)

        async def on_new_volume(data: dict):
            size = int(data.get("size_gb") or 10)
            sc = data.get("storage_class") or (storage_classes[0] if storage_classes else "standard")
            try:
                await self.rag_cfg.add_volume(data.get("name", ""), data.get("directory", "/"), size, sc)
                ui.notify("Volume created", type="positive")
            except Exception as exc:
                ui.notify(f"Error creating PVC: {exc}", type="negative")

        async def on_edit_volume(original: dict, data: dict):
            await asyncio.to_thread(self.rag_cfg.update_volume, original["_id"], data.get("name", ""), data.get("directory", "/"))
            ui.notify("Volume updated", type="positive")

        async def on_delete_volume(item: dict):
            try:
                await self.rag_cfg.delete_volume(item["_id"], item.get("_pvc_name", ""))
                ui.notify("Volume deleted", type="info")
            except Exception as exc:
                ui.notify(f"Error deleting PVC: {exc}", type="negative")

        CRUDTemplate(
            title="Filesystem Volumes",
            columns=["Name", "PVC", "Directory", "Status"],
            on_refresh=refresh_volumes,
            on_new=on_new_volume,
            on_edit=on_edit_volume,
            on_delete=on_delete_volume,
            fields=[
                {"key": "name", "label": "Name", "placeholder": "legal-documents"},
                {"key": "directory", "label": "Directory to scan", "placeholder": "/data/docs"},
            ],
        )

    # ══════════════════════════════════════════════════════════════════════════
    # SFTP
    # ══════════════════════════════════════════════════════════════════════════

    async def _render_sftp_tab(self) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        ui.label("SFTP").classes("text-base font-semibold mb-1")
        ui.label("Multiple credential sets and SFTP paths.").classes("text-xs text-gray-500 mb-3")

        # ── Credentials ───────────────────────────────────────────────────────
        async def refresh_creds():
            return [
                {"name": c["name"], "user": c.get("username", ""), "auth": c.get("auth_type", "password"), "_id": c["id"]}
                for c in await asyncio.to_thread(self.rag_cfg.list_sftp_credentials)
            ]

        async def on_new_cred(data: dict):
            await asyncio.to_thread(
                self.rag_cfg.add_sftp_credential,
                data.get("name", ""), data.get("user", ""), data.get("auth", "password"),
                data.get("password", ""), data.get("private_key", ""),
            )
            ui.notify("SFTP credentials added", type="positive")

        async def on_edit_cred(original: dict, data: dict):
            await asyncio.to_thread(
                self.rag_cfg.update_sftp_credential,
                original["_id"], data.get("name", ""), data.get("user", ""), data.get("auth", "password"),
                data.get("password", ""), data.get("private_key", ""),
            )
            ui.notify("SFTP credentials updated", type="positive")

        async def on_delete_cred(item: dict):
            await asyncio.to_thread(self.rag_cfg.delete_sftp_credential, item["_id"])
            ui.notify("SFTP credentials removed", type="info")

        CRUDTemplate(
            title="SFTP Credentials",
            columns=["Name", "User", "Auth"],
            on_refresh=refresh_creds,
            on_new=on_new_cred,
            on_edit=on_edit_cred,
            on_delete=on_delete_cred,
            fields=[
                {"key": "name", "label": "Name", "placeholder": "docs-server"},
                {"key": "user", "label": "Username", "placeholder": "sftp_user"},
                {"key": "auth", "label": "Auth type", "type": "select", "options": ["password", "key", "both"]},
                {"key": "password", "label": "Password", "type": "password"},
                {"key": "private_key", "label": "Private key (PEM)", "type": "textarea"},
            ],
        )

        ui.separator().classes("my-4")

        # ── SFTP paths ────────────────────────────────────────────────────────
        paths_crud: list = []

        async def _open_conn_modal(item: dict | None = None) -> None:
            cred_opts = {c["id"]: c["name"] for c in await asyncio.to_thread(self.rag_cfg.list_sftp_credentials)}
            is_edit = item is not None
            existing_paths = ",".join(item.get("_paths", [])) if is_edit else ""
            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[500px] p-6 gap-4"):
                    ui.label("Edit SFTP path" if is_edit else "New SFTP path").classes("text-xl font-bold text-slate-800")
                    with ui.column().classes("w-full gap-3"):
                        name_inp = ui.input(label="Name", value=item.get("name", "") if is_edit else "", placeholder="docs-server").classes("w-full").props("outlined dense")
                        host_inp = ui.input(label="Host", value=item.get("_host", "") if is_edit else "", placeholder="sftp.company.com").classes("w-full").props("outlined dense")
                        port_inp = ui.number(label="Port", value=float(item.get("_port", 22)) if is_edit else 22.0).classes("w-full").props("outlined dense")
                        paths_inp = ui.input(label="Paths (comma-separated)", value=existing_paths, placeholder="/data/docs,/shared").classes("w-full").props("outlined dense")
                        cred_keys = list(cred_opts.keys())
                        if not cred_keys:
                            ui.label("⚠ No SFTP credentials configured. Add one above first.").classes("text-sm text-amber-600")
                            cred_sel = None
                        else:
                            default_cred = (item.get("_credential_id", "") if is_edit else "") or cred_keys[0]
                            cred_sel = ui.select(cred_opts, value=default_cred, label="Credentials").classes("w-full").props("outlined dense")
                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")
                        async def _save(d=dlg):
                            if cred_sel is None:
                                ui.notify("Add SFTP credentials first.", type="warning")
                                return
                            paths = [p.strip() for p in paths_inp.value.split(",") if p.strip()]
                            try:
                                if is_edit:
                                    await asyncio.to_thread(self.rag_cfg.update_sftp_connection, item["_id"], name_inp.value, host_inp.value, int(port_inp.value or 22), paths, cred_sel.value)
                                    ui.notify("SFTP path updated", type="positive")
                                else:
                                    await self.rag_cfg.add_sftp_connection(name_inp.value, host_inp.value, int(port_inp.value or 22), paths, cred_sel.value)
                                    ui.notify("SFTP path added", type="positive")
                                d.close()
                                if paths_crud:
                                    paths_crud[0].refresh()
                            except Exception as exc:
                                ui.notify(f"Error: {exc}", type="negative")
                        ui.button("Save", on_click=_save).props("unelevated color=primary")
            dlg.open()

        async def refresh_connections():
            return await asyncio.to_thread(self.rag_cfg.list_sftp_connections)

        async def on_delete_conn(item: dict):
            await self.rag_cfg.delete_sftp_connection(item["_id"])
            ui.notify("SFTP path removed", type="info")

        async def _new_conn():
            await _open_conn_modal()

        tpl = CRUDTemplate(
            title="SFTP Paths",
            columns=["Name", "Host", "Credentials"],
            on_refresh=refresh_connections,
            on_new_click=_new_conn,
            on_edit=_open_conn_modal,
            on_delete=on_delete_conn,
            direct_edit=True,
        )
        paths_crud.append(tpl)


# ══════════════════════════════════════════════════════════════════════════════
# McpView
# ══════════════════════════════════════════════════════════════════════════════


async def _test_mcp_connection(server_id: str, instance: dict, mcp: "MCP") -> tuple[bool, str]:
    """Make a lightweight connectivity check for an MCP server instance.

    Returns (ok, message).
    """
    import httpx
    try:
        if server_id == "git":
            git_cfg = await asyncio.to_thread(mcp.get_git_config)
            repos = git_cfg.get("repositories", [])
            if not repos:
                return False, "No repositories configured"
            repo = repos[0]
            url = repo.get("url", "")
            token = await asyncio.to_thread(mcp.k8s.read, f"git-repo-token-{repo.get('id', '')}") or ""
            headers = {"Authorization": f"token {token}"} if token else {}
            # Detect provider
            if "github.com" in url:
                api = "https://api.github.com/user"
            elif "gitlab" in url:
                from urllib.parse import urlparse
                base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                api = f"{base}/api/v4/projects?per_page=1"
                headers = {"PRIVATE-TOKEN": token} if token else {}
            else:
                return True, "Custom Git — cannot verify automatically"
            async with httpx.AsyncClient() as http:
                r = await http.get(api, headers=headers, timeout=8.0)
            return r.is_success, f"HTTP {r.status_code}"

        inst_id = instance.get("_id", "")
        if server_id == "jira":
            url = instance.get("url", "").rstrip("/")
            email = instance.get("email", "")
            token = await asyncio.to_thread(mcp.get_instance_secret, server_id, inst_id, "token") if inst_id else ""
            if not url:
                return False, "URL not configured"
            async with httpx.AsyncClient() as http:
                r = await http.get(
                    f"{url}/rest/api/3/serverInfo",
                    auth=(email, token) if token else None,
                    timeout=8.0,
                )
            return r.is_success, f"HTTP {r.status_code}"

        if server_id == "confluence":
            url = instance.get("url", "").rstrip("/")
            email = instance.get("email", "")
            token = await asyncio.to_thread(mcp.get_instance_secret, server_id, inst_id, "token") if inst_id else ""
            if not url:
                return False, "URL not configured"
            async with httpx.AsyncClient() as http:
                r = await http.get(
                    f"{url}/rest/api/space?limit=1",
                    auth=(email, token) if token else None,
                    timeout=8.0,
                )
            return r.is_success, f"HTTP {r.status_code}"

        if server_id == "slack":
            token = await asyncio.to_thread(mcp.get_instance_secret, server_id, inst_id, "bot_token") if inst_id else ""
            if not token:
                return False, "Bot token not configured"
            async with httpx.AsyncClient() as http:
                r = await http.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=8.0,
                )
            data = r.json()
            return bool(data.get("ok")), data.get("error") or "OK"

        if server_id == "discord":
            token = await asyncio.to_thread(mcp.get_instance_secret, server_id, inst_id, "bot_token") if inst_id else ""
            if not token:
                return False, "Bot token not configured"
            async with httpx.AsyncClient() as http:
                r = await http.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {token}"},
                    timeout=8.0,
                )
            return r.is_success, f"HTTP {r.status_code}"

        if server_id == "gmail":
            # Gmail uses OAuth — just verify refresh_token is set
            token = await asyncio.to_thread(mcp.get_instance_secret, server_id, inst_id, "refresh_token") if inst_id else ""
            client_id = instance.get("client_id", "")
            if token and client_id:
                return True, "Credentials configured (OAuth flow required for full test)"
            return False, "refresh_token or client_id missing"

    except Exception as exc:
        return False, str(exc)

    return False, "Unknown server"


class McpView:
    """Renders MCP connector configuration using CRUDTemplate.

    Instantiate once per application startup; call _server_tab(sid) per tab.
    """

    def __init__(self) -> None:
        self.k8s = K8s()
        self.fga = OpenFGA()
        self.mcp = MCP(self.k8s, self.fga)
        self.cfg = LiteLLMConfig(self.k8s)

    def _sync_litellm_mcp(self) -> list[str]:
        """Enable a server in LiteLLM iff it has at least one configuration (sync, blocking).

        Returns the list of enabled server IDs.
        """
        enabled = []
        for sid in SERVERS:
            if sid == "git":
                has_cfg = bool(self.mcp.get_git_config().get("repositories"))
            else:
                has_cfg = bool(self.mcp.list_instances(sid))
            if has_cfg:
                enabled.append(sid)
        self.cfg.write_mcp(enabled)
        return enabled

    async def _sync_litellm_mcp_async(self) -> None:
        """Non-blocking wrapper — runs K8s I/O in a thread then syncs FGA access."""
        enabled = await asyncio.to_thread(self._sync_litellm_mcp)
        enabled_set = set(enabled)
        for sid in SERVERS:
            if sid in enabled_set:
                try:
                    await self.fga.write(writes=[
                        {"user": SHOKAN_OBJECT, "relation": "shokan", "object": f"mcp_server:{sid}"},
                        {"user": f"{SHOKAN_OBJECT}#member", "relation": "can_use", "object": f"mcp_server:{sid}"},
                    ])
                except Exception:
                    pass
            else:
                for rel, subj in [
                    ("shokan", SHOKAN_OBJECT),
                    ("can_use", f"{SHOKAN_OBJECT}#member"),
                ]:
                    try:
                        await self.fga.write(deletes=[
                            {"user": subj, "relation": rel, "object": f"mcp_server:{sid}"}
                        ])
                    except Exception:
                        pass

    async def _server_tab(self, server_id: str) -> None:
        meta = SERVER_META.get(server_id, {"icon": "hub", "label": server_id, "description": "", "columns": ["Name"], "fields": []})
        secret_keys = meta.get("secret_keys", [])

        with ui.row().classes("items-center gap-3 mb-3"):
            ui.icon(meta["icon"], size="md").classes("text-gray-500")
            with ui.column().classes("flex-1 gap-0"):
                ui.label(meta["label"]).classes("text-base font-semibold")
                ui.label(meta.get("description", "")).classes("text-xs text-gray-400")

        if server_id == "git":
            self._git_crud()
            return

        # ── Generic multi-instance connector ──────────────────────────────────
        columns = meta["columns"]
        fields = meta["fields"]

        async def refresh_data():
            instances = await asyncio.to_thread(self.mcp.list_instances, server_id)
            return [
                {col.lower().replace(" ", "_"): inst.get(col.lower().replace(" ", "_"), "")
                 for col in columns}
                | {"_id": inst["id"], "_enabled": inst.get("enabled", True)}
                for inst in instances
            ]

        async def on_new(data: dict):
            plain = {k: v for k, v in data.items() if k not in secret_keys}
            secrets = {k: data[k] for k in secret_keys if data.get(k)}
            await asyncio.to_thread(self.mcp.add_instance, server_id, plain, secrets)
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify(f"{meta['label']} connection added", type="positive")

        async def on_edit(original: dict, data: dict):
            inst_id = original["_id"]
            plain = {k: v for k, v in data.items() if k not in secret_keys}
            secrets = {k: data[k] for k in secret_keys if data.get(k)}
            await asyncio.to_thread(self.mcp.update_instance, server_id, inst_id, plain, secrets)
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify(f"{meta['label']} connection updated", type="positive")

        async def on_delete(item: dict):
            await asyncio.to_thread(self.mcp.remove_instance, server_id, item["_id"])
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify(f"{meta['label']} connection removed", type="info")

        CRUDTemplate(
            title=f"{meta['label']} Connections",
            columns=columns,
            on_refresh=refresh_data,
            on_new=on_new,
            on_edit=on_edit,
            on_delete=on_delete,
            fields=fields,
        )

        # ── Test connection ────────────────────────────────────────────────────
        with ui.row().classes("items-center gap-3 mt-2"):
            test_result = ui.label("").classes("text-sm")

            async def run_test(sid=server_id):
                instances = await asyncio.to_thread(self.mcp.list_instances, sid)
                inst = instances[0] if instances else {}
                test_btn.props("loading")
                ok, msg = await _test_mcp_connection(sid, inst, self.mcp)
                test_btn.props(remove="loading")
                if ok:
                    test_result.set_text(f"✓ Connected — {msg}")
                    test_result.classes(remove="text-red-500")
                    test_result.classes("text-green-600")
                else:
                    test_result.set_text(f"✗ {msg}")
                    test_result.classes(remove="text-green-600")
                    test_result.classes("text-red-500")

            test_btn = ui.button("Test connection", icon="wifi_tethering", on_click=run_test).props(
                "outline dense"
            ).classes("text-sm")

    # ── Git ────────────────────────────────────────────────────────────────────

    def _git_crud(self) -> None:
        meta = SERVER_META["git"]

        async def refresh_data():
            cfg = await asyncio.to_thread(self.mcp.get_git_config)
            return [
                {"name": r["name"], "url": r["url"], "auth": r.get("auth", "none"), "_id": r["id"]}
                for r in cfg.get("repositories", [])
            ]

        async def on_new(data: dict):
            await asyncio.to_thread(
                self.mcp.add_git_repo,
                data.get("name", ""), data.get("url", ""),
                data.get("auth", "none"), data.get("token", ""),
            )
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify("Git repository added", type="positive")

        async def on_edit(original: dict, data: dict):
            repo_id = original["_id"]

            def _update():
                cfg = self.mcp.get_git_config()
                for repo in cfg.get("repositories", []):
                    if repo["id"] == repo_id:
                        repo.update({"name": data.get("name", repo["name"]), "url": data.get("url", repo["url"]), "auth": data.get("auth", repo.get("auth", "none"))})
                self.mcp.save_git_config(cfg)
                if data.get("token"):
                    self.k8s.write(f"git-repo-token-{repo_id}", data["token"])

            await asyncio.to_thread(_update)
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify("Git repository updated", type="positive")

        async def on_delete(item: dict):
            await asyncio.to_thread(self.mcp.remove_git_repo, item["_id"])
            asyncio.ensure_future(self._sync_litellm_mcp_async())
            ui.notify("Git repository removed", type="info")

        CRUDTemplate(
            title="Git Repositories",
            columns=meta["columns"],
            on_refresh=refresh_data,
            on_new=on_new,
            on_edit=on_edit,
            on_delete=on_delete,
            fields=meta["fields"],
        )

        with ui.row().classes("items-center gap-3 mt-2"):
            git_result = ui.label("").classes("text-sm")

            async def run_git_test():
                git_test_btn.props("loading")
                ok, msg = await _test_mcp_connection("git", {}, self.mcp)
                git_test_btn.props(remove="loading")
                if ok:
                    git_result.set_text(f"✓ Connected — {msg}")
                    git_result.classes(remove="text-red-500")
                    git_result.classes("text-green-600")
                else:
                    git_result.set_text(f"✗ {msg}")
                    git_result.classes(remove="text-green-600")
                    git_result.classes("text-red-500")

            git_test_btn = ui.button("Test connection", icon="wifi_tethering", on_click=run_git_test).props(
                "outline dense"
            ).classes("text-sm")


# ══════════════════════════════════════════════════════════════════════════════
# DatalakeView
# ══════════════════════════════════════════════════════════════════════════════


class DatalakeView:
    """Renders the Data Lake configuration page.

    Tabs: Amazon S3 · Google Drive · Filesystem · SFTP (RAG cold sources)
          Git · Jira · Confluence · Slack · Gmail · Discord (MCP live connectors)

    Instantiate once per application startup; call render(user, perms) per page visit.
    """

    def __init__(self) -> None:
        self.rag_settings = RagView()
        self.mcp_settings = McpView()

    async def render(self, user: dict, perms: set[str] | None = None) -> None:
        """Build the Data Lake settings UI. Must be called within a NiceGUI page context."""
        if perms is None:
            perms = set()

        ui.label("Data Lake").classes("text-2xl font-bold mb-1")
        ui.label("Cold data sources (RAG) and live connectors (MCP)").classes("text-sm text-gray-500 mb-4")

        # ── Config status ─────────────────────────────────────────────────────────
        rc = self.rag_settings.rag_cfg
        mcp = self.mcp_settings.mcp

        def _build_status():
            return {
                "s3":         bool(rc.list_s3_buckets() or rc.list_s3_credentials()),
                "gdrive":     bool(rc.list_gdrive_folders() or rc.list_gdrive_credentials()),
                "filesystem": bool(rc.list_volumes()),
                "sftp":       bool(rc.list_sftp_connections() or rc.list_sftp_credentials()),
                "git":        bool(mcp.get_git_config().get("repositories")),
                "jira":       bool(mcp.list_instances("jira")),
                "confluence": bool(mcp.list_instances("confluence")),
                "slack":      bool(mcp.list_instances("slack")),
                "gmail":      bool(mcp.list_instances("gmail")),
                "discord":    bool(mcp.list_instances("discord")),
            }

        status = await asyncio.to_thread(_build_status)

        def _visible(source: str) -> bool:
            return can(perms, f"datalake:{source}:read")

        # Build visible tabs
        visible_tabs: list[tuple[str, str, str]] = []  # (source, icon, label)
        _tab_specs = [
            ("s3",        "storage",    "Amazon S3"),
            ("gdrive",    "cloud",      "Google Drive"),
            ("filesystem","folder",     "Filesystem"),
            ("sftp",      "terminal",   "SFTP"),
            ("git",       "code",       "Git"),
            ("jira",      "bug_report", "Jira"),
            ("confluence","article",    "Confluence"),
            ("slack",     "chat",       "Slack"),
            ("gmail",     "email",      "Gmail"),
            ("discord",   "forum",      "Discord"),
        ]
        for source, icon, label in _tab_specs:
            if _visible(source):
                visible_tabs.append((source, icon, label))

        if not visible_tabs:
            ui.label("No Data Lake sources available.").classes("text-gray-500 text-sm p-4")
            return

        tabs_map: dict[str, ui.tab] = {}
        with ui.tabs().classes("w-full bg-gray-100") as tabs:
            for source, icon, label in visible_tabs:
                tabs_map[source] = _status_tab(source, icon, label, status.get(source, False))

        first_tab = tabs_map[visible_tabs[0][0]]
        with ui.tab_panels(tabs, value=first_tab).classes("w-full"):
            for source, _icon, _label in visible_tabs:
                with ui.tab_panel(tabs_map[source]):
                    if source == "s3":
                        await self.rag_settings._render_s3_tab()
                    elif source == "gdrive":
                        await self.rag_settings._render_gdrive_tab()
                    elif source == "filesystem":
                        await self.rag_settings._render_filesystem_tab()
                    elif source == "sftp":
                        await self.rag_settings._render_sftp_tab()
                    else:
                        await self.mcp_settings._server_tab(source)


def _status_tab(name: str, icon: str, label: str, configured: bool) -> ui.tab:
    """Create a tab with a status dot — green if configured, grey if not."""
    dot_class = "text-green-500" if configured else "text-gray-400"
    with ui.tab(name, label="") as tab:
        with ui.row().classes("items-center gap-1 no-wrap"):
            ui.icon(icon, size="xs")
            ui.label(label).classes("text-sm")
            ui.icon("circle", size="xs").classes(dot_class)
    return tab


# ══════════════════════════════════════════════════════════════════════════════
# DataLakePermissions
# ══════════════════════════════════════════════════════════════════════════════

_DS_ROLES = ["owner", "viewer"]


class DataLakePermissions:
    def __init__(self, fga: OpenFGA, rag: RAG) -> None:
        self.fga = fga
        self.rag = rag

    async def render(self, principals: dict[str, str]) -> None:
        from nicegui import context as ng_context
        page_slot = ng_context.client.layout.default_slot
        label_to_key   = {v: k for k, v in principals.items()}
        subject_labels = list(principals.values())

        datasources = await asyncio.to_thread(self.rag.list_datasources)
        ds_by_id    = {ds["id"]: ds.get("name", ds["id"]) for ds in datasources}
        ds_ids      = list(ds_by_id.keys())

        crud: list[CRUDTemplate] = []

        async def refresh_data():
            if not ds_ids:
                return []
            results = await asyncio.gather(*[self.fga.get_object_tuples(f"datasource:{did}") for did in ds_ids])
            rows = []
            for did, tuples in zip(ds_ids, results):
                for subj, rel in tuples.items():
                    if rel not in _DS_ROLES:
                        continue
                    rows.append({
                        "subject":  principals.get(subj, subj),
                        "role":     rel,
                        "resource": ds_by_id.get(did, did),
                        "_subject_key": subj,
                        "_obj":     f"datasource:{did}",
                    })
            return rows

        def open_modal(item: dict | None = None) -> None:
            is_edit      = item is not None
            initial_subj = item["subject"]  if is_edit else (subject_labels[0] if subject_labels else "")
            initial_role = item["role"]     if is_edit and item["role"] in _DS_ROLES else _DS_ROLES[0]
            ds_opts = list(ds_by_id.values()) if ds_by_id else []
            initial_res  = item["resource"] if is_edit else (ds_opts[0] if ds_opts else "")

            with page_slot:
                with ui.dialog() as dlg, ui.card().classes("w-[480px] p-6 gap-4"):
                    ui.label("Edit access" if is_edit else "Grant access").classes("text-xl font-bold text-slate-800")

                    with ui.column().classes("w-full gap-3"):
                        subj_sel = ui.select(
                            subject_labels, value=initial_subj, label="User"
                        ).classes("w-full").props("outlined dense")

                        role_sel = ui.select(
                            _DS_ROLES, value=initial_role, label="Role"
                        ).classes("w-full").props("outlined dense")

                        res_sel = ui.select(
                            ds_opts, value=initial_res if ds_opts else None, label="Data source"
                        ).classes("w-full").props("outlined dense use-input new-value-mode=add")

                        if not ds_opts:
                            ui.label(
                                "No datasources registered — type an ID manually or configure them in Data Lake settings."
                            ).classes("text-xs text-amber-600 -mt-1")

                    with ui.row().classes("justify-end gap-2 mt-2"):
                        ui.button("Cancel", on_click=dlg.close).props("flat")

                        async def save(d=dlg):
                            subj = label_to_key.get(subj_sel.value, subj_sel.value)
                            role = role_sel.value
                            res  = res_sel.value
                            if not subj or not role or not res:
                                ui.notify("All fields are required.", type="warning")
                                return
                            obj = _ds_obj(res, ds_by_id)
                            if is_edit:
                                old_subj = item["_subject_key"]
                                old_role = item["role"]
                                old_obj  = item["_obj"]
                                if subj == old_subj and role == old_role and obj == old_obj:
                                    d.close()
                                    return
                                await self.fga.write(
                                    writes=[{"user": subj, "relation": role, "object": obj}],
                                    deletes=[{"user": old_subj, "relation": old_role, "object": old_obj}],
                                )
                            else:
                                await self.fga.set_relation(subj, role, None, obj)
                            ui.notify("Access saved.", type="positive")
                            d.close()
                            if crud:
                                crud[0].refresh()

                        ui.button("Save", on_click=save).props("unelevated color=primary")

            dlg.open()

        async def on_delete(item: dict):
            await self.fga.remove_relation(item["_subject_key"], item["role"], item["_obj"])
            ui.notify("Access removed.", type="info")

        tpl = CRUDTemplate(
            title="Data Lake access",
            columns=["Subject", "Role", "Resource"],
            on_refresh=refresh_data,
            on_new_click=lambda: open_modal(),
            on_edit=lambda item: open_modal(item),
            on_delete=on_delete,
            direct_edit=True,
        )
        crud.append(tpl)


def _ds_obj(resource_name: str, ds_by_id: dict) -> str:
    """Resolve a datasource display name back to its FGA object id."""
    for did, name in ds_by_id.items():
        if name == resource_name:
            return f"datasource:{did}"
    return f"datasource:{resource_name}"

