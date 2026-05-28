#!/bin/bash
set -e

chmod +x "$(dirname "$0")/utils.sh"
source "$(dirname "$0")/utils.sh"

NAMESPACE="shokanllm"
INSTALLER_DIR="$(cd "$(dirname "$0")" && pwd)"
VALUES_DIR="$INSTALLER_DIR/values"
TOOLS_DIR="$(cd "$INSTALLER_DIR/../tools" && pwd)"
OLLAMA_CHART="$TOOLS_DIR/ollama-helm"
LITELLM_CHART="$TOOLS_DIR/litellm-helm"
KEYCLOAK_CHART="$TOOLS_DIR/keycloak-helm"
CORE_CHART="$TOOLS_DIR/shokan-core-helm"
CORE_DIR="$(cd "$INSTALLER_DIR/../core" && pwd)"

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "unknown")
echo "🔧 Kubernetes context: $CURRENT_CONTEXT"

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker/Colima is not running. Please start it and try again."
  exit 1
fi

BASE_DNS=$(resolve_ingress_base_dns "$CURRENT_CONTEXT")
echo "🌐 Base domain: $BASE_DNS"

# ── Detect cluster RAM and choose default model ────────────────────────────────
NODE_RAM_GB=$(detect_node_ram_gb)
DEFAULT_OLLAMA_MODEL=$(select_default_model "$NODE_RAM_GB")
DEFAULT_LITELLM_MODEL="ollama/${DEFAULT_OLLAMA_MODEL}"
echo "🖥️  Detected cluster RAM: ${NODE_RAM_GB} GB → default model: ${DEFAULT_OLLAMA_MODEL}"

PROJECT_ROOT="$(cd "$INSTALLER_DIR/.." && pwd)"
CREDENTIALS_BACKUP="$PROJECT_ROOT/credentials-backup.age"

create_namespace "$NAMESPACE"
check_helm_installed
check_age_installed

# Add Helm repositories
echo ""
echo "📚 Adding Helm repositories..."
add_helm_repository "qdrant" "https://qdrant.github.io/qdrant-helm"
add_helm_repository "bitnami" "https://charts.bitnami.com/bitnami"
add_helm_repository "openfga" "https://openfga.github.io/helm-charts"

# Generate or retrieve service passwords (stored in K8s secret shokanllm-secret)
echo ""
echo "🔐 Ensuring credentials in secret shokanllm-secret..."
POSTGRES_PASSWORD=$(ensure_db_password "db-postgres-password" "$NAMESPACE")
KEYCLOAK_DB_PASSWORD=$(ensure_db_password "db-keycloak-password" "$NAMESPACE")
OPENFGA_DB_PASSWORD=$(ensure_db_password "db-openfga-password" "$NAMESPACE")
LITELLM_DB_PASSWORD=$(ensure_db_password "db-litellm-password" "$NAMESPACE")
LITELLM_MASTER_KEY=$(ensure_db_password "litellm-master-key" "$NAMESPACE")
KEYCLOAK_ADMIN_PASSWORD=$(ensure_db_password "keycloak-admin-password" "$NAMESPACE")
echo "✅ Credentials ready in secret shokanllm-secret"

# Install Qdrant (Vector Database) - Official Chart
echo ""
echo "================================"
echo "🗄️  Installing Qdrant (Vector Database)"
echo "================================"
install_external_helm_chart "qdrant" "qdrant/qdrant" "$NAMESPACE" "5m"
wait_until_deployment_ready "$NAMESPACE" "qdrant" "300"

# Install Ollama (Local LLM Engine)
echo ""
echo "================================"
echo "🤖 Installing Ollama (Local LLM Engine)"
echo "================================"
install_helm_chart "ollama" "$OLLAMA_CHART" "$NAMESPACE" "5m"
wait_until_deployment_ready "$NAMESPACE" "ollama" "300"
pull_default_ollama_model "$NAMESPACE" "$DEFAULT_OLLAMA_MODEL"
pull_system_ollama_models "$NAMESPACE"

# Install LiteLLM (LLM Proxy Gateway)
echo ""
echo "================================"
echo "🔗 Installing LiteLLM (LLM Proxy Gateway)"
echo "================================"
install_helm_chart "litellm" "$LITELLM_CHART" "$NAMESPACE" "5m"
wait_until_deployment_ready "$NAMESPACE" "litellm" "300"

# Register default model in LiteLLM
LTM_LOCAL_PORT=18082
kubectl port-forward -n "$NAMESPACE" svc/litellm "$LTM_LOCAL_PORT:8000" &>/dev/null &
LTM_PF_PID=$!
sleep 3
register_model_in_litellm \
    "http://localhost:$LTM_LOCAL_PORT" \
    "$DEFAULT_OLLAMA_MODEL" \
    "$DEFAULT_LITELLM_MODEL" \
    "$LITELLM_MASTER_KEY"
kill $LTM_PF_PID 2>/dev/null || true

# Install PostgreSQL
echo ""
echo "================================"
echo "🗄️  Installing PostgreSQL (Database)"
echo "================================"
install_external_helm_chart "postgresql" "bitnami/postgresql" "$NAMESPACE" "5m" \
    "-f $VALUES_DIR/postgresql.yaml --set auth.postgresPassword=$POSTGRES_PASSWORD"
wait_until_deployment_ready "$NAMESPACE" "postgresql" "300"

# Initialize databases and create per-service users
echo ""
echo "================================"
echo "📊 Initializing Databases"
echo "================================"
init_databases "$NAMESPACE"

# Install Keycloak (Authentication)
echo ""
echo "================================"
echo "🔐 Installing Keycloak (Authentication)"
echo "================================"
install_helm_chart "keycloak" "$KEYCLOAK_CHART" "$NAMESPACE" "10m" \
    "-f $VALUES_DIR/keycloak.yaml \
     --set keycloak.hostname=http://keycloak.${BASE_DNS} \
     --set ingress.enabled=true \
     --set ingress.hostname=keycloak.${BASE_DNS} \
     --set ingress.className=nginx"
wait_until_deployment_ready "$NAMESPACE" "keycloak" "300"

# Keycloak post-config: admin user + OIDC client for Core
echo ""
echo "================================"
echo "👤 Configuring Keycloak (users + OIDC client)"
echo "================================"
SHOKAN_ADMIN_USER="shokan-admin"
SHOKAN_ADMIN_TEMP_PASS=$(ensure_db_password "shokan-admin-temp-password" "$NAMESPACE")
ensure_db_password "session-secret" "$NAMESPACE" >/dev/null

KC_LOCAL_PORT=18080
kubectl port-forward -n "$NAMESPACE" svc/keycloak "$KC_LOCAL_PORT:8080" &>/dev/null &
KC_PF_PID=$!
trap "kill $KC_PF_PID 2>/dev/null || true" EXIT

wait_keycloak_api "http://localhost:$KC_LOCAL_PORT" 180

create_keycloak_realm \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD"

create_keycloak_admin_user \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "$SHOKAN_ADMIN_USER" \
    "$SHOKAN_ADMIN_TEMP_PASS"

SHOKAN_SVC_USER="shokan-svc"
SHOKAN_SVC_PASSWORD=$(ensure_db_password "kc-svc-password" "$NAMESPACE")
create_keycloak_service_account \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "$SHOKAN_SVC_USER" \
    "$SHOKAN_SVC_PASSWORD" \
    "$NAMESPACE" \
    "kc-svc-password"

create_keycloak_oidc_client \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "shokan-core" \
    "http://shokan.${BASE_DNS}/auth/callback" \
    "$NAMESPACE" \
    "oidc-client-secret-shokan-core"

kill $KC_PF_PID 2>/dev/null || true
trap - EXIT

# Install OpenFGA (Authorization)
echo ""
echo "================================"
echo "🔓 Installing OpenFGA (Authorization)"
echo "================================"
install_external_helm_chart "openfga" "openfga/openfga" "$NAMESPACE" "5m" \
    "-f $VALUES_DIR/openfga.yaml --set datastore.uri=postgresql://openfga:$OPENFGA_DB_PASSWORD@postgresql:5432/openfga?sslmode=disable" \
    "true"
wait_until_deployment_ready "$NAMESPACE" "openfga" "300"

# Initialize OpenFGA store, model, and structural tuples
echo ""
echo "================================"
echo "🔓 Initializing OpenFGA store"
echo "================================"
FGA_LOCAL_PORT=18080
kubectl port-forward -n "$NAMESPACE" svc/openfga "$FGA_LOCAL_PORT:8080" &>/dev/null &
FGA_PF_PID=$!
sleep 3
init_openfga_store "http://localhost:$FGA_LOCAL_PORT" "$NAMESPACE"

register_model_in_openfga \
    "http://localhost:$FGA_LOCAL_PORT" \
    "$(kubectl get secret shokanllm-secret -n "$NAMESPACE" \
        -o jsonpath='{.data.openfga-store-id}' | base64 --decode)" \
    "$DEFAULT_LITELLM_MODEL"

# Grant platform admin role to the Shokan admin user
FGA_STORE_ID=$(kubectl get secret shokanllm-secret -n "$NAMESPACE" \
    -o jsonpath='{.data.openfga-store-id}' | base64 --decode)
KC_LOCAL_PORT=18081
kubectl port-forward -n "$NAMESPACE" svc/keycloak "$KC_LOCAL_PORT:8080" &>/dev/null &
KC_PF2_PID=$!
sleep 2
init_platform_admin \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "$SHOKAN_ADMIN_USER" \
    "http://localhost:$FGA_LOCAL_PORT" \
    "$FGA_STORE_ID"
kill $KC_PF2_PID 2>/dev/null || true

kill $FGA_PF_PID 2>/dev/null || true

# Build and install Shokan Core (Chainlit UI + OIDC auth)
echo ""
echo "================================"
echo "🧠 Building & Installing Shokan Core"
echo "================================"
build_core_image "$CORE_DIR" "shokan-core:latest"
CORE_VALUES_TMP=$(mktemp /tmp/shokan-core-values-XXXXXX.yaml)
cat > "$CORE_VALUES_TMP" <<EOF
ingress:
  enabled: true
  hostname: "shokan.${BASE_DNS}"
env:
  LITELLM_URL: "http://litellm:8000"
  DEFAULT_MODEL: "${DEFAULT_LITELLM_MODEL}"
  OIDC_CLIENT_ID: "shokan-core"
  SHOKAN_URL: "http://shokan.${BASE_DNS}"
  KEYCLOAK_EXTERNAL_URL: "http://keycloak.${BASE_DNS}"
EOF
install_helm_chart "shokan-core" "$CORE_CHART" "$NAMESPACE" "5m" "-f $CORE_VALUES_TMP"
rm -f "$CORE_VALUES_TMP"
wait_until_deployment_ready "$NAMESPACE" "shokan-core" "180"

# Wait for all deployments to be fully ready
echo ""
echo "================================"
echo "⏳ Waiting for all pods to be ready"
echo "================================"
wait_all_deployments_ready "$NAMESPACE" "600"

verify_all_databases "$NAMESPACE"

echo ""
echo "✅ Installation completed successfully!"
echo ""
echo "📋 Installed services:"
echo ""
echo "   🌐 Externally accessible (Ingress):"
echo "   • Shokan (UI + Settings):  http://shokan.${BASE_DNS}"
echo "   • Keycloak (Auth):         http://keycloak.${BASE_DNS}"
echo ""
echo "   🔒 Cluster-internal only (ClusterIP):"
echo "   • Qdrant, Ollama, LiteLLM, PostgreSQL, OpenFGA"
echo ""
echo "🔑 Keycloak credentials:"
echo "   Shokan admin user:     $SHOKAN_ADMIN_USER"
echo "   Temporary password:    $SHOKAN_ADMIN_TEMP_PASS  ← change on first login"
echo ""

# Backup credentials encrypted with age passphrase
echo "================================"
echo "🔒 Backing up credentials"
echo "================================"
backup_credentials "$NAMESPACE" "$CREDENTIALS_BACKUP"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  SAVE THIS FILE AND YOUR CHOSEN PASSPHRASE"
echo ""
echo "   📦 Backup: $CREDENTIALS_BACKUP"
echo ""
echo "   To restore on a new cluster:"
echo "   bash restore_credentials.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
