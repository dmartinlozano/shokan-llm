# Modelo de permisos — Shokan-LLM

Shokan-LLM utiliza **OpenFGA** (ReBAC — Relationship-Based Access Control) como plano de autorización unificado. Keycloak gestiona la identidad; OpenFGA evalúa los permisos en tiempo real.

El modelo vive en [`model.fga`](./model.fga) y cubre cuatro dominios:

1. **Permisos de Shokan** — quién puede administrar la plataforma.
2. **Datos fríos (RAG)** — qué documentos puede leer cada usuario en Qdrant.
3. **Datos calientes (MCP)** — qué servidores MCP puede usar cada usuario.
4. **Modelos LLM** — qué modelos puede invocar cada usuario vía LiteLLM.

---

## 1. Permisos de Shokan

Controlan el acceso a las operaciones administrativas de la plataforma. El objeto de referencia es siempre `shokan:shokanllm`.

### Roles

| Rol | Descripción |
|---|---|
| `admin` | Acceso completo. Puede gestionar usuarios, servicios y permisos. |
| `member` | Acceso de uso. Puede consultar la IA y ver la configuración. |

Los roles se asignan directamente a usuarios o a grupos de Keycloak:

```
(user:alice,        admin,  shokan:shokanllm)
(user:bob,          member, shokan:shokanllm)
(group:ops#member,  admin,  shokan:shokanllm)
```

### Permisos derivados

| Permiso | admin | member |
|---|---|---|
| `can_manage_users` | ✅ | ❌ |
| `can_manage_services` | ✅ | ❌ |
| `can_manage_datasources` | ✅ | ❌ |
| `can_manage_permissions` | ✅ | ❌ |
| `can_backup` | ✅ | ❌ |
| `can_view_config` | ✅ | ✅ |
| `can_use_ai` | ✅ | ✅ |

### Ejemplo de check en Python

```python
# ¿Puede alice gestionar usuarios?
fga.check(user="user:alice", relation="can_manage_users", object="shokan:shokanllm")

# ¿Puede bob usar la IA?
fga.check(user="user:bob", relation="can_use_ai", object="shokan:shokanllm")
```

---

## 2. Datos fríos (RAG)

Cada fragmento indexado en Qdrant tiene un `doc_id` que corresponde a un objeto `document:<id>` en OpenFGA. Los permisos se resuelven en tres niveles en cascada:

```
can_read(document) =
    viewer(document)           ← permiso directo sobre el documento
    or owner(document)         ← el usuario es propietario
    or can_read(datasource)    ← herencia desde la fuente de datos
```

El conector MCP de cada fuente es responsable de **traducir los permisos de origen a tuplas OpenFGA** en el momento de la ingesta.

El tipo `datasource` usa la relación `shokan: [shokan]` para heredar permisos del tipo `shokan`, por lo que los admins de la plataforma siempre tienen acceso.

### Google Drive

| Permiso en Drive | Tupla OpenFGA |
|---|---|
| Archivo compartido con usuario | `(user:X, viewer, document:gdrive-<id>)` |
| Archivo compartido con grupo de Workspace | `(group:Y#member, viewer, document:gdrive-<id>)` |
| Drive compartido (Shared Drive) | `(group:Y#member, viewer, datasource:gdrive-<drive-id>)` |
| Propietario del archivo | `(user:X, owner, document:gdrive-<id>)` |

**Ejemplo:**

```
(group:ventas#member,  viewer,  datasource:gdrive-ventas)
(document:gdrive-001,  datasource, datasource:gdrive-ventas)
(user:juan,  viewer,  document:gdrive-contrato-001)
```

**Sync:** suscripción a webhooks Drive Activity API para revocar tuplas al eliminar permisos.

---

### Amazon S3

| Permiso en S3 | Tupla OpenFGA |
|---|---|
| Bucket policy para un rol IAM | `(group:iam-role#member, viewer, datasource:s3-<bucket>)` |
| ACL de objeto para usuario | `(user:X, viewer, document:s3-<bucket>-<key>)` |
| Prefijo accesible por un grupo | `(group:Y#member, viewer, datasource:s3-<bucket>-<prefix>)` |

**Sync:** el conector lee políticas de bucket vía AWS SDK en cada ingesta y ante cambios por S3 Event Notifications.

---

### Filesystem

| Permiso Unix | Tupla OpenFGA |
|---|---|
| Propietario del fichero (`owner`) | `(user:X, owner, document:fs-<path-hash>)` |
| Grupo con lectura (`640`, `660`) | `(group:Y#member, viewer, document:fs-<path-hash>)` |
| Directorio accesible por grupo | `(group:Y#member, viewer, datasource:fs-<dir-hash>)` |
| Lectura pública (`644`) | `(group:everyone#member, viewer, document:fs-<path-hash>)` |

**Sync:** `inotify` (Linux) o `FSEvents` (macOS) para detectar cambios de permisos en tiempo real.

---

## 3. Datos calientes (MCP)

Los servidores MCP (Model Context Protocol) dan acceso a datos y acciones en tiempo real. Cada servidor tiene un objeto `mcp_server:<id>` en OpenFGA.

### Servidores disponibles

| ID de objeto | Servidor | Tipo de acceso |
|---|---|---|
| `mcp_server:git` | Repositorios Git | Leer commits, branches, ficheros |
| `mcp_server:jira` | Jira | Leer/crear tickets, proyectos |
| `mcp_server:confluence` | Confluence | Leer páginas de wiki |
| `mcp_server:slack` | Slack | Leer/enviar mensajes |
| `mcp_server:gmail` | Gmail | Leer/enviar correos |
| `mcp_server:gdrive` | Google Drive | Leer/escribir ficheros (live) |
| `mcp_server:s3` | Amazon S3 | Leer/escribir objetos (live) |
| `mcp_server:filesystem` | Filesystem local | Leer/escribir ficheros locales |

### Relaciones

| Relación | Descripción |
|---|---|
| `can_use` | `admin(shokan)` — solo admins de la plataforma |
| `can_configure` | `admin(shokan)` únicamente |
| `can_delete` | `admin(shokan)` únicamente |

El acceso a servidores MCP se controla a través del sistema de roles de Shokan. Los admins de la plataforma heredan automáticamente `can_use` en todos los servidores MCP via la relación `shokan`.

Cada `mcp_server` debe estar vinculado a `shokan:shokanllm` via la relación `shokan`:
```
(mcp_server:git, shokan, shokan:shokanllm)
```

### Ejemplo de tuplas

```
# Toda la plataforma vinculada a Shokan
(mcp_server:git,  shokan, shokan:shokanllm)
(mcp_server:jira, shokan, shokan:shokanllm)
```

### Ejemplo de check en Python

```python
# ¿Puede alice usar el servidor MCP de Git?
fga.check(user="user:alice", relation="can_use", object="mcp_server:git")

# ¿Puede bob configurar el servidor de Jira?
fga.check(user="user:bob", relation="can_configure", object="mcp_server:jira")
```

---

## 4. Modelos LLM

Controlan qué modelos puede invocar cada usuario vía LiteLLM. Cada modelo tiene un objeto `llm_model:<id>`.

### Relaciones

| Relación | Descripción |
|---|---|
| `allowed_user` | Usuario o grupo con acceso explícito al modelo |
| `can_call` | `allowed_user or admin(shokan)` |

Cada `llm_model` debe estar vinculado a `shokan:shokanllm`:
```
(llm_model:ollama-llama3, shokan, shokan:shokanllm)
```

### Ejemplo de tuplas

```
# Modelos vinculados a la plataforma
(llm_model:ollama-llama3, shokan, shokan:shokanllm)
(llm_model:gpt-4o,        shokan, shokan:shokanllm)

# Solo admins y grupo premium pueden usar GPT-4o
(group:premium#member, allowed_user, llm_model:gpt-4o)

# Todos los members pueden usar llama3
(group:all-users#member, allowed_user, llm_model:ollama-llama3)
```

### Ejemplo de check en Python

```python
# ¿Puede alice invocar gpt-4o?
fga.check(user="user:alice", relation="can_call", object="llm_model:gpt-4o")
```

---

## 5. Flujo RAG con filtrado de permisos

```
Consulta del usuario
        │
        ▼
   Core Python
        │
        ├─► Keycloak   Valida JWT → extrae user_id + grupos
        │
        ├─► Qdrant     Búsqueda semántica → top-K fragmentos candidatos (ej. 20)
        │
        ├─► OpenFGA    BatchCheck(user:X, can_read, [doc:1, doc:2, ... doc:20])
        │              Filtra a fragmentos autorizados
        │
        └─► LLM        Recibe solo los fragmentos autorizados → genera respuesta
```

**¿Por qué filtrado post-retrieval?**
Obtener primero los candidatos semánticos y luego filtrar es más eficiente que pre-filtrar, que obligaría a recuperar potencialmente miles de IDs autorizados antes de cada consulta.

---

## 6. Flujo MCP con control de acceso

```
Herramienta MCP solicitada
        │
        ▼
   Core Python
        │
        ├─► OpenFGA    Check(user:X, can_use, mcp_server:<id>)
        │              → 403 si denegado
        │
        ├─► MCP Server Ejecuta la herramienta (git clone, jira search, etc.)
        │
        └─► Core       Incorpora resultado al contexto del LLM
```

---

## 7. Contrato de conectores MCP (datos fríos / RAG)

Todo conector MCP que ingeste documentos DEBE:

1. **Traducir los permisos de origen a tuplas OpenFGA** y escribirlas vía API antes o durante la ingesta.
2. **Indexar en Qdrant** con el `doc_id` como metadato (usado para el check post-retrieval).
3. **Sincronizar revocaciones**: cuando se elimina un permiso en el origen, borrar la tupla correspondiente en OpenFGA.

La propiedad de las tuplas es:
- **Keycloak** → tuplas de membresía de grupo (`user:X, member, group:Y`)
- **Conectores RAG** → tuplas de documento y datasource
- **Admin UI** → tuplas de roles de plataforma (`user:X, admin/member, shokan:shokanllm`) y accesos MCP/LLM

---

## 8. Tuplas de inicialización (requeridas al arrancar)

Estas tuplas estructurales deben escribirse durante la instalación (vía `init_openfga_store` en `utils.sh`):

```
# Vincular todos los objetos a la plataforma Shokan
(mcp_server:git,          shokan, shokan:shokanllm)
(mcp_server:jira,         shokan, shokan:shokanllm)
(mcp_server:confluence,   shokan, shokan:shokanllm)
(mcp_server:slack,        shokan, shokan:shokanllm)
(mcp_server:gmail,        shokan, shokan:shokanllm)
(mcp_server:gdrive,       shokan, shokan:shokanllm)
(mcp_server:s3,           shokan, shokan:shokanllm)
(mcp_server:filesystem,   shokan, shokan:shokanllm)
```

Los modelos LLM y datasources se vinculan dinámicamente desde la UI de settings cuando se configuran.
