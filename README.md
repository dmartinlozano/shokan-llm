# Shokan-LLM

**Shokan-LLM** is an enterprise-grade agentic AI infrastructure platform that turns any Kubernetes cluster into a fully autonomous *Living Data Lake* — a self-contained AI brain that reads, reasons, and acts on your organization's data in real time.

Named after the four-armed warrior Prince Goro, Shokan-LLM embodies the same idea: a single central intelligence operating multiple arms simultaneously, each one reaching into a different data source, tool, or service — all in perfect coordination.

---

## Why Shokan-LLM?

Most enterprise AI projects fail not because of the models, but because of the data gap. LLMs know the world up to their training cutoff, but they are blind to your live systems: your tickets, your repos, your emails, your databases. Shokan-LLM closes that gap.

### Core Capabilities

**Hybrid Context Engine (RAG + MCP)**
Shokan-LLM is the only platform that combines two complementary memory architectures in a single orchestration layer. *Retrieval-Augmented Generation (RAG)* provides deep historical knowledge — your documentation, technical manuals, past incident reports, archived data — stored in a high-performance vector database (Qdrant) and retrieved semantically. *Model Context Protocol (MCP)* provides live, real-time context: the current state of a Jira board, the latest commits in a Git repository, the last message in a Slack channel. The orchestration core intelligently decides which layer to query and how much context to include, minimizing token usage while maximizing answer accuracy.

**Data Sovereignty by Default**
Every AI query stays inside your infrastructure. Shokan-LLM runs LLMs locally through **Ollama**, supporting models like Llama 3, Mistral, Qwen, and others without sending a single token to a third-party cloud. When you need the raw power of frontier models (Claude, GPT-4o, Gemini), you can route specific queries there via **LiteLLM** — with full control over which data leaves your perimeter and which stays local.

**Unified LLM Gateway**
**LiteLLM** acts as a unified proxy that exposes a single OpenAI-compatible API regardless of the backend model. Switch between local Ollama models and cloud models by changing a single configuration value. Run A/B tests across providers. Set fallback chains so that if your local model is under load, requests automatically route to a cloud backup — with full observability and cost tracking.

**Enterprise Security Stack**
Authentication is handled by **Keycloak**, providing full OIDC/OAuth2 support, SSO integration, and user federation with enterprise directories (LDAP, Active Directory). Authorization is managed by **OpenFGA** using a fine-grained, relationship-based access control model inspired by Google Zanzibar — defining who can query which data sources, which LLM models, and which MCP connectors. Security is not an afterthought; it is the foundation.

**Living MCP Connector Ecosystem**
Shokan-LLM ships with a growing library of MCP server connectors that give the AI live access to your operational systems:
- **Development:** Git repositories (local and remote), filesystem operations
- **Project Management:** Jira (issues, sprints, projects), Confluence (pages, spaces)
- **Communication:** Slack (channels, threads, DMs), Gmail, Google Drive
- **Infrastructure:** Amazon S3, Kubernetes-native operations

Each connector is an independent microservice that can be enabled, disabled, and scaled independently — without touching the core orchestration layer.

**Kubernetes-Native Architecture**
The entire platform runs as a set of Helm-managed services in a dedicated `shokanllm` namespace. It works on local development clusters (Minikube), on-premise clusters (K3s, RKE), and all major cloud providers (AKS, GKE, EKS). The installer is idempotent — run it multiple times and it only applies what has changed. Horizontal autoscaling is pre-configured for stateless services. GPU acceleration for Ollama is supported out of the box via the NVIDIA device plugin.

**One-Command Installation**
A guided shell installer handles the entire deployment: it detects your environment (macOS, Linux, cloud provider), installs all dependencies, configures the cluster, generates secure random credentials, deploys all six services in the correct dependency order, and produces a single encrypted backup file of all credentials using `age` encryption. From zero to a running AI platform in under fifteen minutes.

**Automated Credential Management**
All service passwords are randomly generated per installation and stored in a single Kubernetes Secret. The platform includes dedicated scripts for credential backup (encrypted with `age`), credential restoration to a new cluster, and full data backup/restore for all stateful services (PostgreSQL, Qdrant vector collections, Ollama model list).

**Production-Ready Defaults**
Every Helm chart ships with production-oriented values: resource requests and limits, pod disruption budgets, pod anti-affinity rules to spread replicas across nodes, configurable storage classes for fast SSD volumes, TLS termination via cert-manager and Let's Encrypt, and support for private container registries with pull secrets.

---

## System Requirements

- **OS:** macOS (Apple Silicon / Intel) or Linux (Debian / Ubuntu / Fedora)
- **Minimum RAM:** 32 GB (16 GB are reserved for the Minikube cluster)
- **macOS:** Homebrew installed — [brew.sh](https://brew.sh)

All remaining dependencies (`kubectl`, `helm`, `colima`, `docker`, `age`, `minikube`) are installed automatically by the setup script.

---

## Step 1 — Prepare the cluster

Choose the script for your environment:

| Environment | Script |
|---|---|
| Local development (Minikube) | `bash minikube-setup.sh` |
| Real Kubernetes / cloud | `bash k8s-setup.sh` |

---

### Option A — Minikube (local development)

```bash
bash minikube-setup.sh
```

Compatible with macOS and Linux. This script:

1. Verifies the system has at least 32 GB of RAM
2. Installs the required dependencies:
   - **macOS**: via Homebrew (`kubectl`, `helm`, `colima`, `docker`, `age`, `minikube`)
   - **Linux Debian/Ubuntu**: via apt + official repositories
   - **Linux Fedora/RHEL**: via dnf + official repositories
3. **macOS**: starts Colima as the Docker runtime with 16 GB of RAM and half the system CPUs. If it is already running with incorrect memory, it restarts it automatically.
4. **Linux**: starts the native Docker service.
5. Starts **Minikube** with:
   - Driver: Docker
   - Kubernetes: v1.32.0
   - Memory: 15 GB · CPUs: half of the system
6. Enables the `storage-provisioner` and `default-storageclass` addons

---

### Option B — Real Kubernetes / cloud

```bash
bash k8s-setup.sh
```

Compatible with macOS and Linux. Installs the common tools (`kubectl`, `helm`, `age`) and presents a provider menu:

| Option | Provider | Tools installed |
|---|---|---|
| 1 | AKS (Azure) | `azure-cli`, `kubelogin` |
| 2 | GKE (Google) | `google-cloud-sdk`, `gke-gcloud-auth-plugin` |
| 3 | EKS (Amazon) | `aws-cli v2` |
| 4 | Generic (K3s, RKE, on-premise) | Verifies existing connection only |

Depending on the chosen provider, the script prompts for the required details (subscription, project, region, cluster name), authenticates, and configures the `kubectl` context automatically.

At the end it verifies that the cluster is reachable and that a default StorageClass exists (required for persistent volumes).

> **Requirement:** the cluster must already exist. This script only configures the connection, it does not provision infrastructure.

---

## Step 2 — Install Shokan-LLM

```bash
bash installer/install.sh
```

The script installs all 6 services in the `shokanllm` namespace in the correct order, generating random passwords for each:

| Service | Port | Description |
|---|---|---|
| Qdrant | 6333 | Vector database (RAG) |
| Ollama | 11434 | Local LLM engine |
| LiteLLM | 8000 | Unified local/cloud proxy |
| PostgreSQL | 5432 | Shared relational database |
| Keycloak | 8080 | Authentication (OIDC) |
| OpenFGA | 8081 | Authorization |

---

## Credentials and security backup

Passwords are randomly generated on each installation and stored in the Kubernetes secret `shokanllm-secret` (namespace `shokanllm`).

When the installation finishes, `install.sh` generates a **single encrypted backup file** at the root of the project:

```
credentials-backup.age
```

During generation you are prompted for a **passphrase** that protects the file. Store both the file and the passphrase safely.

> `credentials-backup.age` is in `.gitignore` and is never committed.

### What you must keep

| Item | Where to store it |
|---|---|
| `credentials-backup.age` | Password manager, encrypted storage, secure USB |
| The chosen passphrase | Password manager |

Without both items it is not possible to recover credentials if the cluster is lost.

### Viewing active credentials

```bash
# View all stored keys
kubectl get secret shokanllm-secret -n shokanllm -o yaml

# Get a specific password (example: Keycloak admin)
kubectl get secret shokanllm-secret -n shokanllm \
  -o jsonpath='{.data.keycloak-admin-password}' | base64 --decode
```

### Restoring credentials to a new cluster

1. Copy `credentials-backup.age` to the project root on the new machine
2. Run:

```bash
bash restore_credentials.sh
```

You will be prompted for the backup passphrase. Once the secret is restored, reinstall the services:

```bash
bash installer/install.sh
```

`install.sh` detects that the secret already exists and reuses the passwords without regenerating them.

---

## Data backup

Persistent data (PostgreSQL, Qdrant, Ollama models) is backed up with:

```bash
bash backup_data.sh
```

Creates a `backups/TIMESTAMP/` directory with:

| File | Contents |
|---|---|
| `postgresql.sql.gz` | Full dump of all databases |
| `qdrant/` | Snapshots of all vector collections |
| `ollama-models.txt` | List of installed models |

> `backups/` is in `.gitignore` and is never committed.

### Restoring data

```bash
bash restore_data.sh backups/20260510_120000
```

Ollama models are not restored automatically (they can be several GB). The script displays the list and the command to re-download them.

---

## Production deployment (real Kubernetes)

This section covers everything needed to deploy Shokan-LLM on a production Kubernetes cluster (AKS, GKE, EKS, K3s on-premise, or other).

---

### Cluster requirements

#### Local tools

```bash
brew install kubectl helm age   # macOS
# or equivalent on Linux (apt/dnf)
```

#### Recommended node pools

| Pool | Purpose | CPU | RAM | Disk | Count |
|---|---|---|---|---|---|
| `system` | Keycloak, OpenFGA, PostgreSQL, LiteLLM, Qdrant | 4 vCPU | 16 GB | 100 GB SSD | ≥ 2 nodes |
| `llm` | Ollama | 8 vCPU | **≥ 16 GB** | 300 GB SSD | ≥ 1 node |
| `llm-gpu` *(optional)* | Ollama with GPU | 8 vCPU + GPU | ≥ 16 GB | 300 GB SSD | ≥ 1 node |

> Ollama needs RAM proportional to the model. With 16 GB it can run `llama3.2:3b`. For `llama3.1:8b` you need ≥ 32 GB; for `mistral:7b`, ≥ 16 GB with Q4 quantization.

#### StorageClass

The cluster must have a default `StorageClass` that supports `ReadWriteOnce`. Verify it:

```bash
kubectl get storageclass
# The one marked (default) will be used by the installer.
```

For Ollama and Qdrant a fast-disk class (SSD/NVMe) is recommended. Specify it in the production values:

```yaml
# tools/ollama-helm/values-production.yaml
persistence:
  storageClassName: "fast-ssd"   # adjust to your class name
  size: 200Gi
```

#### Ingress controller

The installer expects an NGINX Ingress with an external `LoadBalancer` in the `ingress-nginx` namespace:

```bash
# Install ingress-nginx if not present
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer

# Verify it has an external IP assigned
kubectl get svc ingress-nginx-controller -n ingress-nginx
```

Without an external IP the installer cannot resolve the base domain and will fail.

---

### DNS and domains

The installer automatically detects the external IP of the Ingress and builds domains using [nip.io](https://nip.io):

- `shokan.<IP>.nip.io` — main UI
- `keycloak.<IP>.nip.io` — authentication

For production with a custom domain, point a wildcard DNS record at the Ingress IP:

```
*.shokan.yourcompany.com → <LoadBalancer IP>
```

And configure the installer by exporting the variable before running it:

```bash
export SHOKAN_BASE_DNS="shokan.yourcompany.com"
bash installer/install.sh
```

---

### Configuring replica counts

Each service has its own values file. **Stateless** services scale horizontally; **stateful** ones (Ollama, PostgreSQL, Qdrant) do not.

#### LiteLLM — horizontal scaling (stateless)

```yaml
# tools/litellm-helm/values-production.yaml
replicaCount: 2          # recommended minimum in prod

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

affinity:
  podAntiAffinity:        # forces replicas onto different nodes
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
              - key: app.kubernetes.io/name
                operator: In
                values: [litellm]
          topologyKey: kubernetes.io/hostname
```

#### Keycloak — horizontal scaling (stateless with DB sessions)

```yaml
# tools/keycloak-helm/values-production.yaml
replicaCount: 2
```

#### Ollama — **does not scale horizontally**

Ollama keeps models on a PVC (`ReadWriteOnce`). Only one active pod can read that volume. For multi-node you would need a `ReadWriteMany` PVC (NFS/CephFS) or separate deployments with their own PVCs registered as distinct backends in LiteLLM.

```yaml
# tools/ollama-helm/values-production.yaml
replicaCount: 1           # do not set above 1 with RWO

persistence:
  enabled: true
  size: 200Gi
  storageClassName: "fast-ssd"
```

#### PostgreSQL and Qdrant

Both use StatefulSets with a single pod by default. For high availability:
- **PostgreSQL**: use a chart with replica support (Bitnami HA) or a managed service (Cloud SQL, RDS, Azure Database).
- **Qdrant**: configure cluster mode with `replicaCount > 1` and one PVC per pod.

---

### GPU support for Ollama

If the node has a GPU, configure the environment variable and resources in `values-production.yaml`:

```yaml
# tools/ollama-helm/values-production.yaml
ollama:
  environment:
    OLLAMA_NUM_GPU: "1"     # number of GPUs to use

resources:
  limits:
    nvidia.com/gpu: 1       # requires the NVIDIA device plugin installed

nodeSelector:
  accelerator: nvidia-gpu

tolerations:
  - key: "nvidia.com/gpu"
    operator: "Exists"
    effect: "NoSchedule"
```

Install the NVIDIA plugin in the cluster before deploying:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm install gpu-operator nvidia/gpu-operator -n gpu-operator --create-namespace
```

---

### Automatic model selection based on available RAM

The installer detects the cluster's available RAM and selects the default model:

| Total cluster RAM | Selected model |
|---|---|
| ≥ 16 GB | `llama3.2:3b` |
| ≥ 8 GB | `llama3.2:1b` |
| < 8 GB | `qwen2.5:0.5b` |

To force a specific model, edit Ollama's `values-production.yaml`:

```yaml
ollama:
  models:
    - mistral           # downloaded when the pod starts
    - llama3.2:3b
```

To add models after installation:

```bash
kubectl exec -n shokanllm deploy/ollama -- ollama pull mistral
```

---

### Shokan Core image in a private registry

In non-Minikube environments, the `shokan-core` image must be built locally and pushed to a registry accessible from the cluster:

```bash
# 1. Build
docker build -t your-registry.io/shokan-core:1.0.0 core/

# 2. Push
docker push your-registry.io/shokan-core:1.0.0
```

Then update the chart before installing:

```yaml
# tools/shokan-core-helm/values.yaml  (or a values-production.yaml)
image:
  repository: your-registry.io/shokan-core
  tag: "1.0.0"
  pullPolicy: IfNotPresent    # change from Never to IfNotPresent

imagePullSecrets:
  - name: registry-credentials  # if the registry is private
```

Create the registry secret in the namespace:

```bash
kubectl create secret docker-registry registry-credentials \
  --docker-server=your-registry.io \
  --docker-username=<user> \
  --docker-password=<token> \
  -n shokanllm
```

---

### TLS / HTTPS

Install cert-manager to manage certificates automatically:

```bash
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set crds.enabled=true
```

Create a `ClusterIssuer` for Let's Encrypt:

```yaml
# cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: you@yourcompany.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
```

```bash
kubectl apply -f cluster-issuer.yaml
```

Add the annotation in the Ingress values of each service:

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
  tls:
    - secretName: shokan-tls
      hosts:
        - shokan.yourcompany.com
```

---

### Cloud models in LiteLLM

To add Claude, GPT-4o, or other cloud models, store the API keys as Kubernetes secrets and inject them into the LiteLLM deployment:

```bash
kubectl create secret generic litellm-api-keys \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --from-literal=OPENAI_API_KEY=sk-... \
  -n shokanllm
```

```yaml
# tools/litellm-helm/values-production.yaml
env:
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: litellm-api-keys
        key: ANTHROPIC_API_KEY
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: litellm-api-keys
        key: OPENAI_API_KEY

configMap:
  litellm_config_yaml: |
    model_list:
      - model_name: claude-sonnet
        litellm_params:
          model: claude-sonnet-4-6
      - model_name: gpt-4o
        litellm_params:
          model: gpt-4o
      - model_name: ollama-llama3
        litellm_params:
          model: ollama/llama3.2:3b
          api_base: http://ollama.shokanllm.svc.cluster.local:11434
```

---

### Additional considerations

**Limits and requests:** Production values include `resources.requests` and `resources.limits` for all services. Do not remove them: without them the scheduler cannot guarantee correct placement and Ollama pods may be evicted under memory pressure.

**PodDisruptionBudgets:** For Keycloak and LiteLLM with more than one replica, define a PDB to prevent a node update from taking the service down:

```bash
kubectl create poddisruptionbudget litellm-pdb \
  --selector=app.kubernetes.io/name=litellm \
  --min-available=1 \
  -n shokanllm
```

**Credential backup:** In production, `credentials-backup.age` must be stored outside the cluster (corporate password manager, vault, encrypted S3 bucket). The cluster can be lost; credentials must not be lost with it.

**Component updates:** Chart and image versions are controlled in `installer/versions.yaml`. To update a component, change the version there and re-run `bash installer/install.sh` — the installer uses `helm upgrade --install` and is idempotent.

---

## Accessing the services

```bash
kubectl port-forward -n shokanllm svc/qdrant      6333:6333
kubectl port-forward -n shokanllm svc/ollama      11434:11434
kubectl port-forward -n shokanllm svc/litellm     8000:8000
kubectl port-forward -n shokanllm svc/postgresql  5432:5432
kubectl port-forward -n shokanllm svc/keycloak    8080:8080
kubectl port-forward -n shokanllm svc/openfga     8081:8081
```

---

## Verify the installation

```bash
source installer/utils.sh
verify_installation shokanllm
```

---

## Redeploy core

```bash
minikube image build -t shokan-core:latest core/
kubectl rollout restart deployment/shokan-core -n shokanllm
kubectl rollout status deployment/shokan-core -n shokanllm
```

## Redeploy everything

```bash
minikube delete
./minikube-setup.sh
./installer/install.sh
```
