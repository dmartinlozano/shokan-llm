# Shokan-LLM Permission System Reference

Use this skill whenever creating, modifying or reasoning about access control in Shokan-LLM.

## Authorization stack

| Layer | Technology | Role |
|---|---|---|
| Identity | **Keycloak** | OIDC authentication, user/group management |
| Authorization | **OpenFGA** (ReBAC) | Runtime permission checks, fine-grained rules |
| Storage | **shokanllm-secret** (K8s Secret) | Client secrets, store ID, config JSON blobs |

---

## OpenFGA model — types and objects

Model DSL lives at `core/permissions/model.fga`. The API JSON is embedded in `installer/utils.sh:init_openfga_store`.

### Type: `shokan`
Platform-level administration. **Only one object ever exists: `shokan:shokanllm`.**

| Relation | Who can hold it | Derived permissions |
|---|---|---|
| `admin` | user, group#member | all `can_manage_*`, `can_backup`, `can_view_config`, `can_use_ai` |
| `member` | user, group#member | `can_view_config`, `can_use_ai` |

### Type: `mcp_server` — hot data (live actions)
Objects: `mcp_server:git`, `mcp_server:jira`, `mcp_server:confluence`, `mcp_server:slack`, `mcp_server:gmail`, `mcp_server:gdrive`, `mcp_server:s3`, `mcp_server:filesystem`

Each object requires a structural tuple: `(mcp_server:<id>, shokan, shokan:shokanllm)` — written at install time.

| Relation | Meaning |
|---|---|
| `user_access` | Can invoke tools via this MCP server |
| `operator` | Can use + configure this server |
| `can_use` | `user_access or operator or admin(shokan)` |
| `can_configure` | `operator or admin(shokan)` |
| `can_delete` | `admin(shokan)` only |

### Type: `llm_model` — model access via LiteLLM
Objects: `llm_model:<litellm-model-id>` (e.g. `llm_model:ollama/llama3`, `llm_model:gpt-4o`)

Structural tuple required: `(llm_model:<id>, shokan, shokan:shokanllm)` — written from Settings UI when registering a model.

| Relation | Meaning |
|---|---|
| `allowed_user` | Can call this model |
| `can_call` | `allowed_user or admin(shokan)` |

### Type: `datasource` — cold data (RAG)
Objects: `datasource:gdrive-<id>`, `datasource:s3-<bucket>`, `datasource:fs-<hash>`, etc.

Structural tuple required: `(datasource:<id>, shokan, shokan:shokanllm)` — written from Settings UI.

| Relation | Meaning |
|---|---|
| `viewer` | Can read documents in this datasource |
| `owner` | Full access to documents |
| `can_read` | `viewer or owner or admin(shokan)` |
| `can_ingest` | `owner or admin(shokan)` |
| `can_delete` | `owner or admin(shokan)` |

### Type: `document` — RAG fragments in Qdrant
Objects: `document:gdrive-<id>`, `document:s3-<key>`, `document:fs-<hash>`, etc.

Permission resolves in cascade: direct `viewer/owner` → inherited from `datasource` via `can_read from datasource`.

---

## Python check pattern (in Core or Settings)

```python
import httpx, os

FGA_URL      = os.getenv("OPENFGA_URL",      "http://openfga.shokanllm.svc.cluster.local:8080")
FGA_STORE_ID = os.getenv("OPENFGA_STORE_ID", "")

async def fga_check(user_id: str, relation: str, object_id: str) -> bool:
    async with httpx.AsyncClient() as http:
        r = await http.post(
            f"{FGA_URL}/stores/{FGA_STORE_ID}/check",
            json={"tuple_key": {
                "user": f"user:{user_id}",
                "relation": relation,
                "object": object_id,
            }},
            timeout=5.0,
        )
        return r.is_success and r.json().get("allowed", False)

# Gate admin-only page:
if not await fga_check(user_id, "admin", "shokan:shokanllm"):
    return RedirectResponse("/forbidden")

# Gate MCP tool use:
if not await fga_check(user_id, "can_use", "mcp_server:git"):
    raise PermissionError("No access to Git MCP server")

# Gate model call:
if not await fga_check(user_id, "can_call", f"llm_model:{model_id}"):
    raise PermissionError(f"No access to model {model_id}")
```

---

## RAG: batch permission filtering

```python
async def filter_authorized_docs(user_id: str, doc_ids: list[str]) -> list[str]:
    """Post-retrieval filter: keep only docs the user can read."""
    async with httpx.AsyncClient() as http:
        r = await http.post(
            f"{FGA_URL}/stores/{FGA_STORE_ID}/batch-check",
            json={"checks": [
                {"tuple_key": {"user": f"user:{user_id}", "relation": "can_read", "object": f"document:{d}"}}
                for d in doc_ids
            ]},
            timeout=10.0,
        )
    results = r.json().get("results", [])
    return [doc_ids[i] for i, res in enumerate(results) if res.get("allowed")]
```

---

## Tuple conventions

| Object | User side | Relation |
|---|---|---|
| `shokan:shokanllm` | `user:<kc-sub>` | `admin` or `member` |
| `shokan:shokanllm` | `group:<kc-group-id>#member` | `admin` or `member` |
| `mcp_server:<id>` | `user:<kc-sub>` | `user_access` or `operator` |
| `mcp_server:<id>` | `group:<kc-group-id>#member` | `user_access` or `operator` |
| `llm_model:<id>` | `user:<kc-sub>` | `allowed_user` |
| `datasource:<id>` | `user:<kc-sub>` | `viewer` or `owner` |
| `document:<id>` | `user:<kc-sub>` | `viewer` or `owner` |

**User IDs are Keycloak `sub` UUIDs**, not usernames. Always use the `sub` field from the JWT/userinfo.

---

## Settings page

`core/src/settings_shokan.py` — NiceGUI on port 8081, 4 tabs:

| Tab | What it manages |
|---|---|
| Users & Permissions | KC users + groups → `admin`/`member` on `shokan:shokanllm` |
| MCP Servers | Git repo config (K8s secret) + `user_access`/`operator` per `mcp_server:*` |
| Data Sources (RAG) | `datasource-config` (K8s secret) + `viewer`/`owner` per `datasource:*` |
| LLM Models | Models from LiteLLM + `allowed_user` per `llm_model:*` |

Access: only `admin` on `shokan:shokanllm`. Guard enforced via OIDC callback + OpenFGA check before session is set.

---

## K8s secret keys (shokanllm-secret)

| Key | Contents |
|---|---|
| `openfga-store-id` | OpenFGA store UUID (written at install by `init_openfga_store`) |
| `oidc-client-secret-shokan-core` | OIDC secret for Chainlit app |
| `oidc-client-secret-shokan-settings` | OIDC secret for Settings page |
| `git-mcp-config` | JSON: `{enabled, repositories: [{id, name, url, auth}]}` |
| `git-repo-token-<id>` | Token for Git repo with that ID |
| `datasource-config` | JSON: `{datasources: [{id, name, type, enabled}]}` |
