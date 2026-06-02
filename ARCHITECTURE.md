# Shokan-LLM — Architecture

## Overview

Agentic AI infrastructure platform deployed on a **Kubernetes** cluster (namespace: `shokanllm`). Combines a local LLM (Ollama) with cloud models (via LiteLLM), a vector RAG system (Qdrant), OIDC authentication (Keycloak), fine-grained authorization (OpenFGA), and MCP connectors to external tools.

---

## Components

| Component | Technology | Internal K8s Port | Description |
|---|---|---|---|
| **Shokan Core** | Python / NiceGUI + FastAPI | 7860 | Main application: UI, agent orchestration, REST API |
| **Keycloak** | quay.io/keycloak:26.3.3 | 8080 | IdP / OIDC: user and group management, authentication |
| **OpenFGA** | openfga v1.15.1 | 8080 | Authorization engine (ReBAC): admin/member roles, per-datasource/MCP/model permissions |
| **PostgreSQL** | Bitnami 17.5 | 5432 | Shared relational database for Keycloak and OpenFGA |
| **LiteLLM** | ghcr.io/berriai/litellm | 4000 | Unified cloud model proxy (Claude, GPT-4o, Gemini) |
| **Ollama** | ollama/ollama:latest | 11434 | Local inference engine (Llama3, Mistral, etc.) |
| **Qdrant** | qdrant v1.13.1 | 6333 | Vector database: `shokan_rag` collection, RAG embeddings |
| **K8s Secret** | `shokanllm-secret` | — | Central configuration and credentials store (base64 in K8s) |
| **Ingress** | Kubernetes Ingress | 80/443 | HTTP/S entry point to Shokan Core and external Keycloak |
| **Ingest CronJob** | Python / K8s BatchV1 | — | Periodic pipeline: extracts data → generates embeddings → indexes into Qdrant |

---

## Connection Flows

### 1. Authentication (OIDC)

```
Browser → Ingress → Shokan Core (/login)
Shokan Core → Keycloak (internal: token exchange, JWKS)
Browser → Keycloak external URL (authorize redirect)
Keycloak → Shokan Core (/auth/callback)
Shokan Core → OpenFGA (assign role admin/member on first login)
```

### 2. Chat / Inference

```
Browser → Shokan Core (WebSocket streaming)
Shokan Core → OpenFGA (check: can_use_ai)
Shokan Core → Qdrant (embed query via Ollama → vector search → RAG chunks)
Shokan Core → OpenFGA (filter chunks by datasource:can_read permissions)
Shokan Core → OpenFGA (check: can_use for each MCP server)
Shokan Core → MCPClient (external tool calls)
Shokan Core → Ollama /v1/chat/completions  [if model is ollama/*]
    or
Shokan Core → LiteLLM /chat/completions    [if model is cloud]
LiteLLM → Claude API / OpenAI API / Gemini API  (internet)
Shokan Core → Browser (SSE stream token by token)
```

### 3. MCP Connectors (external tools)

```
MCPClient ← Shokan Core
MCPClient → Git (HTTPS/SSH to remote or local repos)
MCPClient → Jira (Atlassian Cloud API)
MCPClient → Confluence (Atlassian Cloud API)
MCPClient → Slack (Bot API)
MCPClient → Gmail (OAuth2 REST)
MCPClient → Discord (Bot API)
```

MCP credentials stored in `shokanllm-secret` (keys: `mcp-<id>-instances`, `mcp-<id>-inst-<instid>-<field>`).

### 4. RAG Ingest Pipeline (CronJob)

```
Ingest CronJob → K8s Secret (read datasource config: S3, GDrive, Filesystem, SFTP)
Ingest CronJob → External data source (S3 bucket / Google Drive / PVC volume / SFTP)
Ingest CronJob → Ollama /api/embed (model: nomic-embed-text, generates vectors)
Ingest CronJob → Qdrant (write chunks with metadata: datasource_id, file_path)
```

### 5. Configuration Management (Admin UI)

```
Shokan Core → K8s API (read/write shokanllm-secret)
K8s Secret ← → LiteLLM config (providers, router, enabled MCP servers, A2A)
K8s Secret ← → Ollama system models list
K8s Secret ← → Datasource config (RAG)
K8s Secret ← → MCP instances config
Shokan Core → LiteLLM Admin API (add/remove/list cloud models)
Shokan Core → Ollama API (pull/load/unload/delete local models)
Shokan Core → Keycloak Admin REST API (CRUD users and groups)
Shokan Core → OpenFGA (CRUD tuples: roles, datasource permissions, MCP permissions)
Shokan Core → K8s BatchV1 API (schedule/suspend/trigger CronJobs)
Shokan Core → K8s CoreV1 API (PVCs for RAG volumes)
```

### 6. A2A (Agent-to-Agent Protocol)

```
External agent → Shokan Core GET /.well-known/agent.json (agent card)
External agent → Shokan Core POST /a2a/tasks (delegate task)
```

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         USER / BROWSER                          │
└───────────────────────────────┬─────────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼─────────────────────────────────┐
│                      KUBERNETES INGRESS                         │
└──────────┬──────────────────────────────────────┬───────────────┘
           │ :7860                                │ :8080 (ext)
┌──────────▼───────────────┐          ┌───────────▼───────────────┐
│      SHOKAN CORE         │          │        KEYCLOAK            │
│  NiceGUI + FastAPI       │◄─────────│  OIDC / Token / Users      │
│  (main orchestrator)     │          │  Groups / Realms           │
└──────┬───────┬──────┬────┘          └────────────┬──────────────┘
       │       │      │                            │ SQL
       │       │      │                 ┌──────────▼──────────────┐
       │       │      │            ┌────►      POSTGRESQL         │
       │       │      │            │    │  (Keycloak + OpenFGA DB) │
       │       │      │            │    └─────────────────────────┘
       │       │      │  ┌─────────┴─────────────────────────────┐
       │       │      └──►           OPENFGA                     │
       │       │         │  ReBAC Authorization                  │
       │       │         │  (roles, datasources, MCP, models)    │
       │       │         └───────────────────────────────────────┘
       │       │
       │  ┌────▼──────────────────────────────────────────────────┐
       │  │                  LLM INTELLIGENCE LAYER               │
       │  │  ┌────────────────────┐   ┌──────────────────────┐   │
       │  │  │      OLLAMA        │   │      LITELLM          │   │
       │  │  │  (local: Llama3,   │   │  (cloud proxy):       │   │
       │  │  │   Mistral, etc.)   │   │  Claude/GPT/Gemini    │───┼──► Internet
       │  │  │  :11434            │   │  :4000                │   │
       │  │  └────────────────────┘   └──────────────────────┘   │
       │  └───────────────────────────────────────────────────────┘
       │
   ┌───▼───────────────────────────────────────────────────────────┐
   │                 RAG + STATIC MEMORY LAYER                     │
   │  ┌────────────────────┐   ┌───────────────────────────────┐  │
   │  │      QDRANT        │   │      INGEST CRONJOB           │  │
   │  │  Vector DB         │◄──│  S3 / GDrive / FS / SFTP      │  │
   │  │  :6333             │   │  → Ollama embed → Qdrant       │  │
   │  └────────────────────┘   └───────────────────────────────┘  │
   └───────────────────────────────────────────────────────────────┘
       │
   ┌───▼───────────────────────────────────────────────────────────┐
   │                  MCP LAYER (EXTERNAL TOOLS)                   │
   │   Git  │  Jira  │  Confluence  │  Slack  │  Gmail  │ Discord  │
   └───────────────────────────────────────────────────────────────┘
       │
   ┌───▼───────────────────────────────────────────────────────────┐
   │              K8S SECRET (shokanllm-secret)                    │
   │  Central config: providers, MCP instances, datasources,       │
   │  system models, router config, encrypted credentials          │
   └───────────────────────────────────────────────────────────────┘
```

---

## Design Notes

- **Everything runs in the same K8s namespace** (`shokanllm`). Internal URLs use `<service>.shokanllm.svc.cluster.local`.
- **The K8s Secret is the configuration bus**: there is no application-layer database; all config is persisted in `shokanllm-secret` via the Kubernetes API.
- **Dual LLM route**: Ollama (local, no internet egress) and LiteLLM (cloud, requires internet + API keys). Routing is decided by the `ollama/` prefix in the model name.
- **OpenFGA controls all access**: who can chat, which datasources a user can read, which MCP servers they can invoke, which models are available.
- **Keycloak is the sole IdP**: manages users and groups; Keycloak UUIDs are the same identifiers used by OpenFGA for authorization tuples.
- **RAG is asynchronous**: the CronJob indexes in the background; the chat queries Qdrant in real time with per-`datasource_id` permission filtering.
- **MCP is synchronous inside the agentic loop**: up to 5 tool-calling rounds before generating the final streaming answer.
- **Chat history condensation**: history is automatically summarized by an LLM when it exceeds 20 messages, always keeping the 8 most recent verbatim.
