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

# Install LiteLLM (LLM Proxy Gateway)
echo ""
echo "================================"
echo "🔗 Installing LiteLLM (LLM Proxy Gateway)"
echo "================================"
install_helm_chart "litellm" "$LITELLM_CHART" "$NAMESPACE" "5m"
wait_until_deployment_ready "$NAMESPACE" "litellm" "300"

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
KC_HOSTNAME_VALUES=$(mktemp /tmp/keycloak-hostname-XXXXXX.yaml)
cat > "$KC_HOSTNAME_VALUES" <<EOF
extraEnvVars:
  - name: KC_HOSTNAME
    value: "keycloak.${BASE_DNS}"
  - name: KC_HOSTNAME_STRICT
    value: "false"
  - name: KC_HOSTNAME_STRICT_BACKCHANNEL
    value: "false"
EOF
install_external_helm_chart "keycloak" "bitnami/keycloak" "$NAMESPACE" "5m" \
    "-f $VALUES_DIR/keycloak.yaml \
     -f $KC_HOSTNAME_VALUES \
     --set auth.adminPassword=$KEYCLOAK_ADMIN_PASSWORD \
     --set externalDatabase.password=$KEYCLOAK_DB_PASSWORD \
     --set ingress.enabled=true \
     --set ingress.hostname=keycloak.${BASE_DNS} \
     --set ingress.ingressClassName=nginx"
rm -f "$KC_HOSTNAME_VALUES"
wait_until_deployment_ready "$NAMESPACE" "keycloak" "300"

# Keycloak post-config: admin user + OIDC client for Core
echo ""
echo "================================"
echo "👤 Configuring Keycloak (users + OIDC client)"
echo "================================"
SHOKAN_ADMIN_USER="shokan-admin"
SHOKAN_ADMIN_TEMP_PASS=$(ensure_db_password "shokan-admin-temp-password" "$NAMESPACE")
ensure_db_password "chainlit-auth-secret" "$NAMESPACE" >/dev/null

KC_LOCAL_PORT=18080
kubectl port-forward -n "$NAMESPACE" svc/keycloak "$KC_LOCAL_PORT:8080" &>/dev/null &
KC_PF_PID=$!
trap "kill $KC_PF_PID 2>/dev/null || true" EXIT

wait_keycloak_api "http://localhost:$KC_LOCAL_PORT" 180

create_keycloak_admin_user \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "$SHOKAN_ADMIN_USER" \
    "$SHOKAN_ADMIN_TEMP_PASS"

create_keycloak_oidc_client \
    "http://localhost:$KC_LOCAL_PORT" \
    "$KEYCLOAK_ADMIN_PASSWORD" \
    "shokan-core" \
    "http://shokan.${BASE_DNS}/auth/oauth/custom/callback" \
    "$NAMESPACE"

kill $KC_PF_PID 2>/dev/null || true
trap - EXIT

# Install OpenFGA (Authorization)
echo ""
echo "================================"
echo "🔓 Installing OpenFGA (Authorization)"
echo "================================"
install_external_helm_chart "openfga" "openfga/openfga" "$NAMESPACE" "5m" \
    "-f $VALUES_DIR/openfga.yaml --set datastore.uri=postgresql://openfga:$OPENFGA_DB_PASSWORD@postgresql:5432/openfga?sslmode=disable"
wait_until_deployment_ready "$NAMESPACE" "openfga" "300"

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
  LITELLM_BASE_URL: "http://litellm:8000"
  SHOKAN_MODEL: "ollama/llama3"
  OAUTH_CUSTOM_CLIENT_ID: "shokan-core"
  OAUTH_CUSTOM_AUTHORIZATION_URL: "http://keycloak.${BASE_DNS}/realms/master/protocol/openid-connect/auth"
  OAUTH_CUSTOM_TOKEN_URL: "http://keycloak.${NAMESPACE}.svc.cluster.local:8080/realms/master/protocol/openid-connect/token"
  OAUTH_CUSTOM_USERINFO_URL: "http://keycloak.${NAMESPACE}.svc.cluster.local:8080/realms/master/protocol/openid-connect/userinfo"
  OAUTH_CUSTOM_DEFAULT_SCOPES: "openid profile email"
  OAUTH_CUSTOM_DISPLAY_NAME: "Shokan SSO"
  CHAINLIT_URL: "http://shokan.${BASE_DNS}"
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
echo "📋 Servicios instalados:"
echo ""
echo "   🌐 Accesibles externamente (Ingress):"
echo "   • Shokan Core (UI):  http://shokan.${BASE_DNS}"
echo "   • Keycloak (Auth):   http://keycloak.${BASE_DNS}"
echo ""
echo "   🔒 Solo accesibles dentro del cluster (ClusterIP):"
echo "   • Qdrant, Ollama, LiteLLM, PostgreSQL, OpenFGA"
echo ""
echo "🔑 Credenciales Keycloak:"
echo "   Usuario admin Shokan: $SHOKAN_ADMIN_USER"
echo "   Contraseña temporal:  $SHOKAN_ADMIN_TEMP_PASS  ← cámbiala en el primer login"
echo ""

# Backup credentials encrypted with age passphrase
echo "================================"
echo "🔒 Backing up credentials"
echo "================================"
backup_credentials "$NAMESPACE" "$CREDENTIALS_BACKUP"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  GUARDA ESTE FICHERO Y LA CONTRASEÑA ELEGIDA"
echo ""
echo "   📦 Backup: $CREDENTIALS_BACKUP"
echo ""
echo "   Para restaurar en un cluster nuevo:"
echo "   bash restore_credentials.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
