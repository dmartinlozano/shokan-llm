# Shokan-LLM

Plataforma de infraestructura de IA agéntica sobre Kubernetes. Orquesta un *Data Lake vivo* combinando RAG (conocimiento histórico) con MCP (datos en tiempo real), ejecutando modelos LLM localmente mediante Ollama o en cloud mediante LiteLLM.

**Servicios incluidos:** Qdrant · Ollama · LiteLLM · PostgreSQL · Keycloak · OpenFGA

---

## Requisitos del sistema

- **SO:** macOS (Apple Silicon / Intel) o Linux (Debian/Ubuntu/Fedora)
- **RAM mínima:** 32 GB (16 GB se reservan para Minikube)
- **macOS:** Homebrew instalado — [brew.sh](https://brew.sh)

El resto de dependencias (kubectl, helm, colima, docker, age, minikube) las instala automáticamente el script de preparación.

---

## Paso 1 — Preparar el cluster

Elige el script según tu entorno:

| Entorno | Script |
|---|---|
| Desarrollo local (Minikube) | `bash minikube-setup.sh` |
| Kubernetes real / cloud | `bash k8s-setup.sh` |

---

### Opción A — Minikube (desarrollo local)

```bash
bash minikube-setup.sh
```

Compatible con macOS y Linux. Este script:

1. Verifica que el sistema tenga al menos 32 GB de RAM
2. Instala las dependencias necesarias:
   - **macOS**: vía Homebrew (`kubectl`, `helm`, `colima`, `docker`, `age`, `minikube`)
   - **Linux Debian/Ubuntu**: vía apt + repositorios oficiales
   - **Linux Fedora/RHEL**: vía dnf + repositorios oficiales
3. **macOS**: arranca Colima como runtime Docker con 16 GB de RAM y la mitad de los CPUs. Si ya está corriendo con memoria incorrecta, lo reinicia automáticamente.
4. **Linux**: arranca el servicio Docker nativo.
5. Arranca **Minikube** con:
   - Driver: Docker
   - Kubernetes: v1.32.0
   - Memoria: 15 GB · CPUs: mitad del sistema
6. Activa los addons `storage-provisioner` y `default-storageclass`

---

### Opción B — Kubernetes real / cloud

```bash
bash k8s-setup.sh
```

Compatible con macOS y Linux. Instala las herramientas comunes (`kubectl`, `helm`, `age`) y presenta un menú de proveedores:

| Opción | Proveedor | Herramientas instaladas |
|---|---|---|
| 1 | AKS (Azure) | `azure-cli`, `kubelogin` |
| 2 | GKE (Google) | `google-cloud-sdk`, `gke-gcloud-auth-plugin` |
| 3 | EKS (Amazon) | `aws-cli v2` |
| 4 | Generic (K3s, RKE, on-premise) | Solo verifica la conexión actual |

Según el proveedor elegido, el script pide los datos necesarios (suscripción, proyecto, región, nombre del cluster), autentica y configura el contexto `kubectl` automáticamente.

Al terminar verifica que el cluster es accesible y que existe una StorageClass por defecto (necesaria para los volúmenes persistentes).

> **Requisito:** el cluster debe existir previamente. Este script solo configura la conexión, no crea infraestructura.

---

## Paso 2 — Instalar Shokan-LLM

```bash
bash installer/install.sh
```

El script instala los 6 servicios en el namespace `shokanllm` en el orden correcto, generando contraseñas aleatorias para cada uno:

| Servicio | Puerto | Descripción |
|---|---|---|
| Qdrant | 6333 | Base de datos vectorial (RAG) |
| Ollama | 11434 | Motor LLM local |
| LiteLLM | 8000 | Proxy unificado local/cloud |
| PostgreSQL | 5432 | Base de datos compartida |
| Keycloak | 8080 | Autenticación (OIDC) |
| OpenFGA | 8081 | Autorización |

---

## Credenciales y backup de seguridad

Las contraseñas se generan aleatoriamente en cada instalación y se almacenan en el secret de Kubernetes `shokanllm-secret` (namespace `shokanllm`).

Al finalizar la instalación, `install.sh` genera un **único fichero de backup cifrado** en la raíz del proyecto:

```
credentials-backup.age
```

Durante la generación se te pide una **contraseña** que protege el fichero. Guárdalas junto con el fichero.

> `credentials-backup.age` está en `.gitignore` y nunca se commitea.

### Qué debes guardar

| Qué | Dónde guardarlo |
|---|---|
| `credentials-backup.age` | Gestor de contraseñas, almacenamiento cifrado, USB seguro |
| La contraseña elegida | Gestor de contraseñas |

Sin ambas cosas no es posible recuperar las credenciales si el cluster se pierde.

### Consultar credenciales activas

```bash
# Ver todas las claves almacenadas
kubectl get secret shokanllm-secret -n shokanllm -o yaml

# Obtener una contraseña específica (ejemplo: admin de Keycloak)
kubectl get secret shokanllm-secret -n shokanllm \
  -o jsonpath='{.data.keycloak-admin-password}' | base64 --decode
```

### Recuperar credenciales en un cluster nuevo

1. Copia `credentials-backup.age` a la raíz del proyecto en la nueva máquina
2. Ejecuta:

```bash
bash restore_credentials.sh
```

Se te pedirá la contraseña del backup. Una vez restaurado el secret, vuelve a instalar los servicios:

```bash
bash installer/install.sh
```

`install.sh` detecta que el secret ya existe y reutiliza las contraseñas sin regenerarlas.

---

## Backup de datos

Los datos persistentes (PostgreSQL, Qdrant, modelos Ollama) se respaldan con:

```bash
bash backup_data.sh
```

Crea un directorio `backups/TIMESTAMP/` con:

| Fichero | Contenido |
|---|---|
| `postgresql.sql.gz` | Volcado completo de todas las bases de datos |
| `qdrant/` | Snapshots de todas las colecciones vectoriales |
| `ollama-models.txt` | Lista de modelos instalados |

> `backups/` está en `.gitignore` y nunca se commitea.

### Restaurar datos

```bash
bash restore_data.sh backups/20260510_120000
```

Los modelos de Ollama no se restauran automáticamente (pueden ser varios GB). El script muestra la lista y el comando para re-descargarlos.

---

## Acceso a los servicios

```bash
kubectl port-forward -n shokanllm svc/qdrant      6333:6333
kubectl port-forward -n shokanllm svc/ollama      11434:11434
kubectl port-forward -n shokanllm svc/litellm     8000:8000
kubectl port-forward -n shokanllm svc/postgresql  5432:5432
kubectl port-forward -n shokanllm svc/keycloak    8080:8080
kubectl port-forward -n shokanllm svc/openfga     8081:8081
```

---

## Verificar la instalación

```bash
source installer/utils.sh
verify_installation shokanllm
```
