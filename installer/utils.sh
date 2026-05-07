create_namespace() {
    local namespace="$1"
    if kubectl get namespace "$namespace" &>/dev/null; then
        echo "Namespace '$namespace' already exists."
    else
        kubectl create namespace "$namespace"
    fi
}

check_helm_installed() {
    if ! command -v helm &> /dev/null; then
        echo "❌ Helm is not installed. Please install Helm first."
        echo "   Visit: https://helm.sh/docs/intro/install/"
        exit 1
    fi
}

check_age_installed() {
    if ! command -v age &>/dev/null || ! command -v age-keygen &>/dev/null; then
        echo "❌ age is not installed."
        echo "   macOS: brew install age"
        echo "   Linux: https://github.com/FiloSottile/age/releases"
        exit 1
    fi
}

create_keycloak_oidc_client() {
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local client_id="$3"
    local redirect_uri="$4"
    local namespace="$5"

    echo "🔑 Configuring Keycloak OIDC client '$client_id'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

    [[ -z "$token" ]] && { echo "❌ Could not get admin token for OIDC client setup"; return 1; }

    # Create confidential client — ignore 409 if already exists
    curl -sf -X POST "$keycloak_url/admin/realms/master/clients" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"clientId\":\"$client_id\",\"enabled\":true,\"protocol\":\"openid-connect\",\"publicClient\":false,\"standardFlowEnabled\":true,\"directAccessGrantsEnabled\":false,\"redirectUris\":[\"$redirect_uri\"],\"webOrigins\":[\"+\"]}" \
        >/dev/null 2>&1 || true

    local client_uuid
    client_uuid=$(curl -sf "$keycloak_url/admin/realms/master/clients?clientId=${client_id}" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)

    [[ -z "$client_uuid" ]] && { echo "❌ Could not retrieve client UUID for '$client_id'"; return 1; }

    local client_secret
    client_secret=$(curl -sf "$keycloak_url/admin/realms/master/clients/$client_uuid/client-secret" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['value'])" 2>/dev/null)

    [[ -z "$client_secret" ]] && { echo "❌ Could not retrieve client secret for '$client_id'"; return 1; }

    kubectl patch secret -n "$namespace" "shokanllm-secret" \
        -p "{\"data\":{\"oidc-client-secret-shokan-core\":\"$(echo -n "$client_secret" | base64)\"}}" >/dev/null

    echo "✅ OIDC client '$client_id' configured"
}

wait_all_deployments_ready() {
    local namespace="$1"
    local timeout="${2:-600}"

    echo "⏳ Confirming all deployments are ready in namespace '$namespace'..."
    local deployments
    deployments=$(kubectl get deployments -n "$namespace" -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    for deployment in $deployments; do
        if ! kubectl rollout status deployment/"$deployment" -n "$namespace" --timeout="${timeout}s"; then
            echo "❌ Deployment '$deployment' is not ready."
            kubectl get pods -n "$namespace"
            exit 1
        fi
    done
    echo "✅ All deployments ready in namespace '$namespace'"
}

wait_keycloak_api() {
    local url="$1"
    local timeout="${2:-180}"

    echo "⏳ Waiting for Keycloak API at $url..."
    for ((i=0; i<timeout; i+=5)); do
        if curl -sf "$url/health/ready" >/dev/null 2>&1; then
            echo "✅ Keycloak API is ready"
            return 0
        fi
        sleep 5
    done
    echo "❌ Keycloak API not ready after $timeout seconds"
    return 1
}

create_keycloak_admin_user() {
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local username="$3"
    local temp_password="$4"

    echo "👤 Creating Shokan admin user '$username' in Keycloak..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

    if [[ -z "$token" ]]; then
        echo "❌ Could not obtain Keycloak admin token. Check bootstrap credentials."
        return 1
    fi

    # Create user (idempotent — ignore 409 Conflict if already exists)
    local http_status
    http_status=$(curl -sf -o /dev/null -w "%{http_code}" \
        -X POST "$keycloak_url/admin/realms/master/users" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$username\",\"enabled\":true,\"emailVerified\":true}" 2>/dev/null || echo "000")

    if [[ "$http_status" != "201" && "$http_status" != "409" ]]; then
        echo "❌ Failed to create user (HTTP $http_status)"
        return 1
    fi

    # Get user ID
    local user_id
    user_id=$(curl -sf "$keycloak_url/admin/realms/master/users?username=${username}&exact=true" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)

    if [[ -z "$user_id" ]]; then
        echo "❌ Could not retrieve user ID for '$username'"
        return 1
    fi

    # Assign realm-management admin role
    local role_id
    role_id=$(curl -sf "$keycloak_url/admin/realms/master/roles/admin" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

    if [[ -n "$role_id" ]]; then
        curl -sf -X POST "$keycloak_url/admin/realms/master/users/$user_id/role-mappings/realm" \
            -H "Authorization: Bearer $token" \
            -H "Content-Type: application/json" \
            -d "[{\"id\":\"$role_id\",\"name\":\"admin\"}]" >/dev/null 2>&1 || true
    fi

    # Set temporary password
    curl -sf -X PUT "$keycloak_url/admin/realms/master/users/$user_id/reset-password" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"password\",\"value\":\"${temp_password}\",\"temporary\":true}" >/dev/null

    echo "✅ Keycloak admin user '$username' ready (temporary password — change on first login)"
}

build_core_image() {
    local context_dir="$1"   # path to core/
    local image_tag="${2:-shokan-core:latest}"
    local current_context
    current_context=$(kubectl config current-context 2>/dev/null || echo "unknown")

    echo "🔨 Building Shokan Core image: $image_tag"

    if [[ "$current_context" == "minikube" ]]; then
        minikube image build -t "$image_tag" "$context_dir"
    else
        docker build -t "$image_tag" "$context_dir"
        echo "⚠️  Real cluster detected: push the image to your registry before deploying."
        echo "   docker push $image_tag"
    fi

    echo "✅ Image built: $image_tag"
}

install_helm_chart() {
    local release_name="$1"
    local chart_path="$2"
    local namespace="$3"
    local timeout="${4:-5m}"
    local extra_values="${5:-}"

    echo "📦 Installing Helm chart: $release_name from $chart_path in namespace $namespace..."

    if ! helm lint "$chart_path" &>/dev/null; then
        echo "❌ Helm chart validation failed for $chart_path"
        exit 1
    fi

    local install_cmd="helm upgrade --install \"$release_name\" \"$chart_path\" --namespace \"$namespace\" --wait --timeout \"$timeout\""
    if [[ -n "$extra_values" ]]; then
        install_cmd="$install_cmd $extra_values"
    fi

    if eval "$install_cmd" &>/dev/null; then
        echo "✅ Helm chart '$release_name' installed successfully"
    else
        echo "❌ Failed to install Helm chart '$release_name'"
        exit 1
    fi
}

resolve_ingress_base_dns() {
    local context="$1"

    if [[ "$context" == "minikube" ]]; then
        local ip
        ip=$(minikube ip 2>/dev/null)
        if [[ -z "$ip" ]]; then
            echo "❌ Could not get Minikube IP. Is Minikube running?" >&2
            return 1
        fi
        echo "${ip}.nip.io"
        return 0
    fi

    # Real cluster: prefer IP, fall back to hostname (EKS)
    local ingress_ip ingress_host
    ingress_ip=$(kubectl get svc ingress-nginx-controller -n ingress-nginx \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    ingress_host=$(kubectl get svc ingress-nginx-controller -n ingress-nginx \
        -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)

    if [[ -n "$ingress_ip" ]]; then
        echo "${ingress_ip}.nip.io"
    elif [[ -n "$ingress_host" ]]; then
        echo "$ingress_host"
    else
        echo "❌ Ingress controller has no external IP. Run bash k8s-setup.sh first." >&2
        return 1
    fi
}

wait_until_deployment_ready() {
    local namespace="$1"
    local deployment_name="$2"
    local timeout="${3:-300}"  # Default timeout is 300 seconds

    echo "⏳ Waiting for deployment '$deployment_name' in namespace '$namespace' to be ready..."
    if ! kubectl rollout status deployment/"$deployment_name" -n "$namespace" --timeout="${timeout}s"; then
        echo "❌ Deployment '$deployment_name' did not become ready within $timeout seconds."
        exit 1
    fi
    echo "✅ Deployment '$deployment_name' is ready."
}

add_helm_repository() {
    local repo_name="$1"
    local repo_url="$2"

    echo "📦 Adding Helm repository: $repo_name ($repo_url)"
    
    if helm repo add "$repo_name" "$repo_url" 2>/dev/null; then
        echo "✅ Helm repository '$repo_name' added successfully"
    else
        # Repository might already exist, try update
        if helm repo update "$repo_name" 2>/dev/null; then
            echo "✅ Helm repository '$repo_name' updated successfully"
        else
            echo "❌ Failed to add/update Helm repository '$repo_name'"
            exit 1
        fi
    fi
}

install_external_helm_chart() {
    local release_name="$1"
    local repo_chart="$2"
    local namespace="$3"
    local timeout="${4:-5m}"
    local extra_values="${5:-}"

    echo "📦 Installing Helm chart: $release_name from $repo_chart in namespace $namespace..."
    
    # Ensure helm repo is updated
    helm repo update &>/dev/null || true
    
    # Build install command
    local install_cmd="helm upgrade --install \"$release_name\" \"$repo_chart\" --namespace \"$namespace\" --wait --timeout \"$timeout\""
    
    if [[ -n "$extra_values" ]]; then
        install_cmd="$install_cmd $extra_values"
    fi
    
    # Execute install
    if eval "$install_cmd" &>/dev/null; then
        echo "✅ Helm chart '$release_name' installed successfully"
    else
        echo "❌ Failed to install Helm chart '$release_name'"
        exit 1
    fi
}

verify_database_exists() {
    local namespace="$1"
    local db_name="$2"
    
    echo "🔍 Verificando que BBDD '$db_name' existe en PostgreSQL..."
    
    if kubectl exec deployment/postgresql -n "$namespace" -- \
        psql -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" | grep -q "1"; then
        echo "✅ BBDD '$db_name' existe en PostgreSQL"
        return 0
    else
        echo "❌ BBDD '$db_name' NO existe en PostgreSQL"
        return 1
    fi
}

verify_all_databases() {
    local namespace="$1"
    local missing_dbs=0
    
    echo ""
    echo "📊 Verificando todas las bases de datos requeridas..."
    
    for db_name in "keycloak" "openfga" "litellm"; do
        if ! verify_database_exists "$namespace" "$db_name"; then
            ((missing_dbs++))
        fi
    done
    
    if [[ $missing_dbs -gt 0 ]]; then
        echo ""
        echo "⚠️  Se encontraron $missing_dbs bases de datos faltantes"
        echo "💡 Ejecute: bash $(dirname "$0")/init-databases.sh $namespace"
        return 1
    else
        echo ""
        echo "✅ Todas las bases de datos requeridas existen"
        return 0
    fi
}

wait_for_database_ready() {
    local namespace="$1"
    local timeout="${2:-300}"

    echo "⏳ Esperando a que PostgreSQL esté listo para crear BBDDs..."

    for ((i=0; i<timeout; i+=10)); do
        if kubectl exec deployment/postgresql -n "$namespace" -- \
            psql -U postgres -d postgres -c "SELECT 1" &>/dev/null; then
            echo "✅ PostgreSQL está listo"
            return 0
        fi
        echo "   Reintentando en 10 segundos... ($i/$timeout)"
        sleep 10
    done

    echo "❌ PostgreSQL no está listo después de $timeout segundos"
    return 1
}

# ============================================================
# Output colors
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================
# Deployment verification helpers
# ============================================================

check_deployment() {
    local namespace="$1"
    local deployment="$2"
    local ready desired
    ready=$(kubectl get deployment "$deployment" -n "$namespace" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    desired=$(kubectl get deployment "$deployment" -n "$namespace" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
    if [[ "$ready" -eq "$desired" ]] && [[ "$desired" -gt 0 ]]; then
        echo -e "${GREEN}✅${NC} $deployment: $ready/$desired replicas ready"
    else
        echo -e "${RED}❌${NC} $deployment: $ready/$desired replicas ready"
    fi
}

check_service() {
    local namespace="$1"
    local service="$2"
    local port="$3"
    if kubectl get service "$service" -n "$namespace" &>/dev/null; then
        echo -e "${GREEN}✅${NC} Service $service available on port $port"
    else
        echo -e "${RED}❌${NC} Service $service not found"
    fi
}

check_pvc() {
    local namespace="$1"
    local pvc="$2"
    local status
    status=$(kubectl get pvc "$pvc" -n "$namespace" -o jsonpath='{.status.phase}' 2>/dev/null || echo "NOT_FOUND")
    if [[ "$status" == "Bound" ]]; then
        echo -e "${GREEN}✅${NC} PVC $pvc is Bound"
    else
        echo -e "${RED}❌${NC} PVC $pvc status: $status"
    fi
}

check_database() {
    local namespace="$1"
    local db_name="$2"
    if kubectl exec deployment/postgresql -n "$namespace" -- \
        psql -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" 2>/dev/null | grep -q "1"; then
        echo -e "${GREEN}✅${NC} Database $db_name exists"
    else
        echo -e "${RED}❌${NC} Database $db_name not found"
    fi
}

# ============================================================
# Password management
# ============================================================

generate_password() {
    openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 24
}

get_db_password() {
    local key="$1"
    local namespace="$2"
    local encoded
    encoded=$(kubectl get secret -n "$namespace" shokanllm-secret -o jsonpath="{.data.$key}" 2>/dev/null)
    if [[ -z "$encoded" ]]; then
        echo "❌ Secret key '$key' not found in namespace '$namespace'." >&2
        return 1
    fi
    echo "$encoded" | base64 --decode
}

ensure_db_password() {
    local key="$1"
    local namespace="$2"
    local encoded existing new_password
    encoded=$(kubectl get secret -n "$namespace" shokanllm-secret -o jsonpath="{.data.$key}" 2>/dev/null)
    existing=$(echo "$encoded" | base64 --decode 2>/dev/null)
    if [[ -n "$existing" ]]; then
        echo "$existing"
        return 0
    fi
    new_password=$(generate_password)
    if kubectl get secret -n "$namespace" "shokanllm-secret" &>/dev/null; then
        kubectl patch secret -n "$namespace" "shokanllm-secret" \
            -p "{\"data\":{\"$key\":\"$(echo -n "$new_password" | base64)\"}}" >/dev/null
    else
        kubectl create secret generic -n "$namespace" "shokanllm-secret" \
            --from-literal="$key=$new_password" >/dev/null
    fi
    echo "$new_password"
}

# ============================================================
# Database initialization
# ============================================================

init_databases() {
    local namespace="${1:-shokanllm}"

    local kc_pass fga_pass ltm_pass
    kc_pass=$(get_db_password "db-keycloak-password" "$namespace")
    fga_pass=$(get_db_password "db-openfga-password" "$namespace")
    ltm_pass=$(get_db_password "db-litellm-password" "$namespace")

    echo "🗄️  Inicializando bases de datos en PostgreSQL..."
    echo ""
    echo "⏳ Esperando a que PostgreSQL esté disponible..."
    kubectl rollout status deployment/postgresql -n "$namespace" --timeout=300s

    echo "📝 Ejecutando SQL de inicialización..."
    kubectl exec -i deployment/postgresql -n "$namespace" -- \
        psql -U postgres -d postgres \
        -v "kc_pass=$kc_pass" \
        -v "fga_pass=$fga_pass" \
        -v "ltm_pass=$ltm_pass" <<'EOSQL'
CREATE DATABASE keycloak WITH OWNER postgres ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;
CREATE DATABASE openfga WITH OWNER postgres ENCODING 'UTF8';
CREATE DATABASE litellm WITH OWNER postgres ENCODING 'UTF8';
CREATE USER keycloak WITH PASSWORD :'kc_pass';
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
CREATE USER openfga WITH PASSWORD :'fga_pass';
GRANT ALL PRIVILEGES ON DATABASE openfga TO openfga;
CREATE USER litellm WITH PASSWORD :'ltm_pass';
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm;
\c keycloak
GRANT ALL ON SCHEMA public TO keycloak;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\c openfga
GRANT ALL ON SCHEMA public TO openfga;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\c litellm
GRANT ALL ON SCHEMA public TO litellm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\l
EOSQL

    echo ""
    echo "✅ Bases de datos e usuarios inicializados: keycloak  •  openfga  •  litellm"
}

# ============================================================
# Full installation verification
# ============================================================

verify_installation() {
    local namespace="${1:-shokanllm}"
    echo "🔍 Verificando instalación de Shokan-LLM en namespace: $namespace"
    echo ""
    echo "📦 Deployments:"
    check_deployment "$namespace" "qdrant"
    check_deployment "$namespace" "ollama"
    check_deployment "$namespace" "litellm"
    check_deployment "$namespace" "postgresql"
    check_deployment "$namespace" "keycloak"
    check_deployment "$namespace" "openfga"
    echo ""
    echo "🔌 Services:"
    check_service "$namespace" "qdrant" "6333"
    check_service "$namespace" "ollama" "11434"
    check_service "$namespace" "litellm" "8000"
    check_service "$namespace" "postgresql" "5432"
    check_service "$namespace" "keycloak" "8080"
    check_service "$namespace" "openfga" "8081"
    echo ""
    echo "💾 Persistent Volumes:"
    check_pvc "$namespace" "postgresql-data"
    check_pvc "$namespace" "qdrant-data"
    check_pvc "$namespace" "ollama-data"
    echo ""
    echo "🗄️  Databases in PostgreSQL:"
    check_database "$namespace" "keycloak"
    check_database "$namespace" "openfga"
    check_database "$namespace" "litellm"
    echo ""
    echo "📋 Recent pod events:"
    kubectl get events -n "$namespace" --sort-by='.lastTimestamp' | tail -10
    echo ""
    echo "🔍 Checking for errors in logs:"
    local deployment errors
    for deployment in "qdrant" "ollama" "litellm" "postgresql" "keycloak" "openfga"; do
        errors=$(kubectl logs -n "$namespace" deployment/"$deployment" --tail=50 2>/dev/null | grep -i "error" | head -1 || echo "")
        if [[ -n "$errors" ]]; then
            echo -e "${YELLOW}⚠️ ${NC}$deployment: $errors"
        fi
    done
    echo ""
    echo "✅ Verification complete!"
    echo ""
    echo "💡 Next steps:"
    echo "   1. Port-forward: kubectl port-forward -n $namespace svc/keycloak 8080:8080"
    echo "   2. Access Keycloak: http://localhost:8080"
    echo "   3. Retrieve admin password:"
    echo "      kubectl get secret shokanllm-secret -n $namespace -o jsonpath='{.data.keycloak-admin-password}' | base64 --decode"
    echo ""
}

# ============================================================
# Credentials backup and restore (age passphrase encryption)
# ============================================================

backup_credentials() {
    local namespace="$1"
    local output_file="$2"

    echo ""
    echo "🔒 Creating encrypted credentials backup..."
    echo "   Choose a passphrase to protect the file."
    echo "   You will need it to restore credentials on a new cluster."
    echo ""

    kubectl get secret shokanllm-secret -n "$namespace" -o yaml \
        | sed '/^\s*\(resourceVersion\|uid\|creationTimestamp\|generation\|selfLink\|managedFields\):/d' \
        | age --passphrase -o "$output_file"

    chmod 600 "$output_file"
    echo ""
    echo "✅ Backup saved: $output_file"
}

restore_credentials() {
    local namespace="$1"
    local input_file="$2"

    if [[ ! -f "$input_file" ]]; then
        echo "❌ Backup file not found: $input_file"
        exit 1
    fi

    create_namespace "$namespace"
    echo "🔓 Restoring credentials from: $input_file"
    echo "   Enter the backup passphrase..."
    age -d "$input_file" | kubectl apply -n "$namespace" -f -
    echo "✅ Credentials restored to namespace: $namespace"
}

# ============================================================
# Data backup and restore (PostgreSQL, Qdrant, Ollama)
# ============================================================

backup_postgresql() {
    local namespace="$1"
    local output_file="$2"

    echo "🗄️  Backing up PostgreSQL (all databases)..."
    kubectl rollout status deployment/postgresql -n "$namespace" --timeout=60s &>/dev/null
    kubectl exec deployment/postgresql -n "$namespace" -- \
        pg_dumpall -U postgres | gzip > "$output_file"
    echo "✅ PostgreSQL backup: $output_file ($(du -sh "$output_file" | cut -f1))"
}

restore_postgresql() {
    local namespace="$1"
    local input_file="$2"

    if [[ ! -f "$input_file" ]]; then
        echo "❌ PostgreSQL backup not found: $input_file"
        return 1
    fi

    echo "🗄️  Restoring PostgreSQL from: $input_file"
    kubectl rollout status deployment/postgresql -n "$namespace" --timeout=120s &>/dev/null
    gunzip -c "$input_file" | kubectl exec -i deployment/postgresql -n "$namespace" -- \
        psql -U postgres -d postgres
    echo "✅ PostgreSQL restored"
}

backup_qdrant() {
    local namespace="$1"
    local output_dir="$2"
    local port=16333

    echo "🔍 Backing up Qdrant collections..."

    kubectl port-forward -n "$namespace" svc/qdrant "$port:6333" &>/dev/null &
    local pf_pid=$!
    sleep 3

    mkdir -p "$output_dir"

    local collections
    collections=$(curl -s "http://localhost:$port/collections" | \
        python3 -c "import sys,json; [print(c['name']) for c in json.load(sys.stdin)['result']['collections']]" 2>/dev/null || true)

    if [[ -z "$collections" ]]; then
        echo "   No Qdrant collections found (or Qdrant not reachable)."
        kill "$pf_pid" 2>/dev/null || true
        return 0
    fi

    local col
    for col in $collections; do
        echo "   Snapshotting collection: $col"
        local snapshot_name
        snapshot_name=$(curl -s -X POST "http://localhost:$port/collections/$col/snapshots" | \
            python3 -c "import sys,json; print(json.load(sys.stdin)['result']['name'])" 2>/dev/null)
        if [[ -n "$snapshot_name" ]]; then
            curl -s "http://localhost:$port/collections/$col/snapshots/$snapshot_name" \
                -o "$output_dir/${col}.snapshot"
            echo "   ✅ $col → ${col}.snapshot"
        else
            echo "   ⚠️  Failed to snapshot collection: $col"
        fi
    done

    kill "$pf_pid" 2>/dev/null || true
    echo "✅ Qdrant backup complete: $output_dir"
}

restore_qdrant() {
    local namespace="$1"
    local input_dir="$2"
    local port=16333

    if [[ ! -d "$input_dir" ]]; then
        echo "❌ Qdrant backup directory not found: $input_dir"
        return 1
    fi

    echo "🔍 Restoring Qdrant collections from: $input_dir"

    kubectl port-forward -n "$namespace" svc/qdrant "$port:6333" &>/dev/null &
    local pf_pid=$!
    sleep 3

    local snapshot_file
    for snapshot_file in "$input_dir"/*.snapshot; do
        [[ -f "$snapshot_file" ]] || continue
        local col
        col=$(basename "$snapshot_file" .snapshot)
        echo "   Restoring collection: $col"
        curl -s -X POST "http://localhost:$port/collections/$col/snapshots/upload?priority=snapshot" \
            -H "Content-Type: multipart/form-data" \
            -F "snapshot=@$snapshot_file" | \
            python3 -c "import sys,json; r=json.load(sys.stdin); print('   ✅' if r.get('result') else '   ⚠️  ' + str(r))" 2>/dev/null || \
            echo "   ⚠️  Failed to restore $col"
    done

    kill "$pf_pid" 2>/dev/null || true
    echo "✅ Qdrant restore complete"
}

backup_ollama_models() {
    local namespace="$1"
    local output_file="$2"

    echo "🤖 Saving Ollama model list..."
    kubectl exec deployment/ollama -n "$namespace" -- \
        ollama list 2>/dev/null > "$output_file" || {
        echo "   ⚠️  Could not list Ollama models (pod may not be ready)"
        return 0
    }
    echo "✅ Ollama model list: $output_file"
}

backup_data() {
    local namespace="${1:-shokanllm}"
    local project_root="${2:-$(pwd)}"
    local timestamp
    timestamp=$(date +"%Y%m%d_%H%M%S")
    local backup_dir="$project_root/backups/$timestamp"

    mkdir -p "$backup_dir"

    echo "================================"
    echo "📦 Shokan-LLM Data Backup"
    echo "   Destination: $backup_dir"
    echo "================================"
    echo ""

    backup_postgresql "$namespace" "$backup_dir/postgresql.sql.gz"
    echo ""
    backup_qdrant "$namespace" "$backup_dir/qdrant"
    echo ""
    backup_ollama_models "$namespace" "$backup_dir/ollama-models.txt"

    echo ""
    echo "================================"
    echo "✅ Backup completed: $backup_dir"
    echo "   $(du -sh "$backup_dir" | cut -f1) total"
    echo "================================"
}

restore_data() {
    local namespace="${1:-shokanllm}"
    local backup_dir="$2"
    local project_root="${3:-$(pwd)}"

    if [[ -z "$backup_dir" ]]; then
        echo "Usage: restore_data <namespace> <backup_directory>"
        echo ""
        echo "Available backups:"
        ls -1d "$project_root/backups/"*/ 2>/dev/null || echo "   (none found in backups/)"
        return 1
    fi

    if [[ ! -d "$backup_dir" ]]; then
        echo "❌ Backup directory not found: $backup_dir"
        return 1
    fi

    echo "================================"
    echo "♻️  Shokan-LLM Data Restore"
    echo "   Source: $backup_dir"
    echo "================================"
    echo ""

    if [[ -f "$backup_dir/postgresql.sql.gz" ]]; then
        restore_postgresql "$namespace" "$backup_dir/postgresql.sql.gz"
    else
        echo "⚠️  No PostgreSQL backup found, skipping."
    fi

    echo ""
    if [[ -d "$backup_dir/qdrant" ]]; then
        restore_qdrant "$namespace" "$backup_dir/qdrant"
    else
        echo "⚠️  No Qdrant backup found, skipping."
    fi

    echo ""
    if [[ -f "$backup_dir/ollama-models.txt" ]]; then
        echo "🤖 Ollama models from backup:"
        cat "$backup_dir/ollama-models.txt"
        echo ""
        echo "   To re-pull: kubectl exec -n $namespace deployment/ollama -- ollama pull <model>"
    fi

    echo ""
    echo "================================"
    echo "✅ Restore completed"
    echo "================================"
}