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

SHOKAN_KC_REALM="shokanllm"

create_keycloak_realm() {
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local realm="${3:-$SHOKAN_KC_REALM}"

    echo "🌐 Creating Keycloak realm '$realm'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

    [[ -z "$token" ]] && { echo "❌ Could not get admin token for realm creation"; return 1; }

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$keycloak_url/admin/realms" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"realm\":\"$realm\",\"enabled\":true,\"displayName\":\"Shokan LLM\"}")

    if [[ "$http_status" == "201" ]]; then
        echo "✅ Realm '$realm' created"
    elif [[ "$http_status" == "409" ]]; then
        echo "ℹ️  Realm '$realm' already exists"
    else
        echo "❌ Failed to create realm '$realm' (HTTP $http_status)"
        return 1
    fi
}

create_keycloak_oidc_client() {
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local client_id="$3"
    local redirect_uri="$4"
    local namespace="$5"
    local secret_key="${6:-oidc-client-secret-${client_id}}"
    local realm="${7:-$SHOKAN_KC_REALM}"

    echo "🔑 Configuring Keycloak OIDC client '$client_id' in realm '$realm'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

    [[ -z "$token" ]] && { echo "❌ Could not get admin token for OIDC client setup"; return 1; }

    # Derive the app base URL from the redirect URI (strip /auth/callback)
    local app_base="${redirect_uri%/auth/callback}"

    # Create confidential client — ignore 409 if already exists
    curl -sf -X POST "$keycloak_url/admin/realms/$realm/clients" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"clientId\":\"$client_id\",\"enabled\":true,\"protocol\":\"openid-connect\",\"publicClient\":false,\"standardFlowEnabled\":true,\"directAccessGrantsEnabled\":false,\"redirectUris\":[\"$redirect_uri\"],\"webOrigins\":[\"+\"],\"attributes\":{\"post.logout.redirect.uris\":\"$app_base/login\"}}" \
        >/dev/null 2>&1 || true

    local client_uuid
    client_uuid=$(curl -sf "$keycloak_url/admin/realms/$realm/clients?clientId=${client_id}" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)

    [[ -z "$client_uuid" ]] && { echo "❌ Could not retrieve client UUID for '$client_id'"; return 1; }

    local client_secret
    client_secret=$(curl -sf "$keycloak_url/admin/realms/$realm/clients/$client_uuid/client-secret" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['value'])" 2>/dev/null)

    [[ -z "$client_secret" ]] && { echo "❌ Could not retrieve client secret for '$client_id'"; return 1; }

    kubectl patch secret -n "$namespace" "shokanllm-secret" \
        -p "{\"data\":{\"${secret_key}\":\"$(echo -n "$client_secret" | base64)\"}}" >/dev/null

    echo "✅ OIDC client '$client_id' configured (secret key: $secret_key)"
}

init_platform_admin() {
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local admin_username="$3"   # e.g. shokan-admin
    local openfga_url="$4"
    local store_id="$5"
    local realm="${6:-$SHOKAN_KC_REALM}"

    echo "🛡️  Granting platform admin role to '$admin_username'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
    [[ -z "$token" ]] && { echo "❌ Could not get admin token"; return 1; }

    local user_id
    user_id=$(curl -sf "$keycloak_url/admin/realms/$realm/users?username=${admin_username}&exact=true" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)
    [[ -z "$user_id" ]] && { echo "❌ Could not find user '$admin_username' in realm '$realm'"; return 1; }

    curl -sf -X POST "$openfga_url/stores/$store_id/write" \
        -H "Content-Type: application/json" \
        -d "{\"writes\":{\"tuple_keys\":[{\"user\":\"user:$user_id\",\"relation\":\"admin\",\"object\":\"shokan:shokanllm\"}]}}" \
        >/dev/null 2>&1 || true

    echo "✅ Platform admin tuple written for '$admin_username' (id: $user_id)"
}

init_openfga_store() {
    local openfga_url="$1"
    local namespace="$2"

    # Idempotent — skip if store already exists in the secret
    local existing_id
    existing_id=$(kubectl get secret shokanllm-secret -n "$namespace" \
        -o jsonpath='{.data.openfga-store-id}' 2>/dev/null | base64 --decode 2>/dev/null || true)
    if [[ -n "$existing_id" ]]; then
        echo "✅ OpenFGA store already exists: $existing_id"
        return 0
    fi

    echo "🗄️  Creating OpenFGA store 'shokanllm'..."
    local store_id
    store_id=$(curl -sf -X POST "$openfga_url/stores" \
        -H "Content-Type: application/json" \
        -d '{"name":"shokanllm"}' \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

    [[ -z "$store_id" ]] && { echo "❌ Failed to create OpenFGA store"; return 1; }

    echo "📋 Uploading authorization model..."
    # Write model using OpenFGA API JSON format
    local model_response
    model_response=$(curl -sf -X POST "$openfga_url/stores/$store_id/authorization-models" \
        -H "Content-Type: application/json" \
        -d '{
  "schema_version": "1.1",
  "type_definitions": [
    {"type": "user", "relations": {}},
    {
      "type": "group",
      "relations": {"member": {"this": {}}},
      "metadata": {"relations": {"member": {"directly_related_user_types": [{"type": "user"}]}}}
    },
    {
      "type": "shokan",
      "relations": {
        "admin":  {"this": {}},
        "member": {"this": {}},
        "can_manage_users":       {"computedUserset": {"relation": "admin"}},
        "can_manage_services":    {"computedUserset": {"relation": "admin"}},
        "can_manage_datasources": {"computedUserset": {"relation": "admin"}},
        "can_manage_permissions": {"computedUserset": {"relation": "admin"}},
        "can_backup":             {"computedUserset": {"relation": "admin"}},
        "can_view_config": {"union": {"child": [{"computedUserset": {"relation": "admin"}}, {"computedUserset": {"relation": "member"}}]}},
        "can_use_ai":      {"union": {"child": [{"computedUserset": {"relation": "admin"}}, {"computedUserset": {"relation": "member"}}]}}
      },
      "metadata": {"relations": {
        "admin":  {"directly_related_user_types": [{"type": "user"}, {"type": "group", "relation": "member"}]},
        "member": {"directly_related_user_types": [{"type": "user"}, {"type": "group", "relation": "member"}]},
        "can_manage_users": {"directly_related_user_types": []},
        "can_manage_services": {"directly_related_user_types": []},
        "can_manage_datasources": {"directly_related_user_types": []},
        "can_manage_permissions": {"directly_related_user_types": []},
        "can_backup": {"directly_related_user_types": []},
        "can_view_config": {"directly_related_user_types": []},
        "can_use_ai": {"directly_related_user_types": []}
      }}
    },
    {
      "type": "mcp_server",
      "relations": {
        "shokan":        {"this": {}},
        "allowed_role":  {"this": {}},
        "allowed_user":  {"this": {}},
        "can_use":       {"union": {"child": [{"computedUserset": {"relation": "allowed_user"}}, {"computedUserset": {"relation": "allowed_role"}}, {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}]}},
        "can_configure": {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}},
        "can_delete":    {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}
      },
      "metadata": {"relations": {
        "shokan":        {"directly_related_user_types": [{"type": "shokan"}]},
        "allowed_role":  {"directly_related_user_types": [{"type": "shokan", "relation": "admin"}, {"type": "shokan", "relation": "member"}]},
        "allowed_user":  {"directly_related_user_types": [{"type": "user"}]},
        "can_use":       {"directly_related_user_types": []},
        "can_configure": {"directly_related_user_types": []},
        "can_delete":    {"directly_related_user_types": []}
      }}
    },
    {
      "type": "llm_model",
      "relations": {
        "shokan":       {"this": {}},
        "allowed_user": {"this": {}},
        "can_call": {"union": {"child": [{"computedUserset": {"relation": "allowed_user"}}, {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}]}}
      },
      "metadata": {"relations": {
        "shokan":       {"directly_related_user_types": [{"type": "shokan"}]},
        "allowed_user": {"directly_related_user_types": [{"type": "user"}]},
        "can_call": {"directly_related_user_types": []}
      }}
    },
    {
      "type": "datasource",
      "relations": {
        "shokan": {"this": {}},
        "owner":  {"this": {}},
        "viewer": {"this": {}},
        "can_ingest": {"union": {"child": [{"computedUserset": {"relation": "owner"}}, {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}]}},
        "can_read":   {"union": {"child": [{"computedUserset": {"relation": "viewer"}}, {"computedUserset": {"relation": "owner"}}, {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}]}},
        "can_delete": {"union": {"child": [{"computedUserset": {"relation": "owner"}}, {"tupleToUserset": {"tupleset": {"relation": "shokan"}, "computedUserset": {"relation": "admin"}}}]}}
      },
      "metadata": {"relations": {
        "shokan": {"directly_related_user_types": [{"type": "shokan"}]},
        "owner":  {"directly_related_user_types": [{"type": "user"}]},
        "viewer": {"directly_related_user_types": [{"type": "user"}]},
        "can_ingest": {"directly_related_user_types": []},
        "can_read":   {"directly_related_user_types": []},
        "can_delete": {"directly_related_user_types": []}
      }}
    },
    {
      "type": "document",
      "relations": {
        "datasource": {"this": {}},
        "owner":      {"this": {}},
        "viewer":     {"this": {}},
        "can_read":   {"union": {"child": [{"computedUserset": {"relation": "viewer"}}, {"computedUserset": {"relation": "owner"}}, {"tupleToUserset": {"tupleset": {"relation": "datasource"}, "computedUserset": {"relation": "can_read"}}}]}},
        "can_delete": {"union": {"child": [{"computedUserset": {"relation": "owner"}}, {"tupleToUserset": {"tupleset": {"relation": "datasource"}, "computedUserset": {"relation": "can_delete"}}}]}}
      },
      "metadata": {"relations": {
        "datasource": {"directly_related_user_types": [{"type": "datasource"}]},
        "owner":      {"directly_related_user_types": [{"type": "user"}]},
        "viewer":     {"directly_related_user_types": [{"type": "user"}]},
        "can_read":   {"directly_related_user_types": []},
        "can_delete": {"directly_related_user_types": []}
      }}
    }
    ,{
      "type": "ui_permission",
      "relations": {
        "allowed_role": {"this": {}},
        "allowed_user": {"this": {}},
        "can_use": {"union": {"child": [{"computedUserset": {"relation": "allowed_user"}}, {"computedUserset": {"relation": "allowed_role"}}]}}
      },
      "metadata": {"relations": {
        "allowed_role": {"directly_related_user_types": [{"type": "shokan", "relation": "admin"}, {"type": "shokan", "relation": "member"}]},
        "allowed_user": {"directly_related_user_types": [{"type": "user"}]},
        "can_use": {"directly_related_user_types": []}
      }}
    }
  ]
}' 2>/dev/null)

    local model_id
    model_id=$(echo "$model_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('authorization_model_id',''))" 2>/dev/null || true)
    [[ -z "$model_id" ]] && { echo "⚠️  Could not confirm model upload — check OpenFGA logs"; }

    # Write structural tuples: link all MCP servers to shokan:shokanllm
    echo "📝 Writing structural tuples..."
    curl -sf -X POST "$openfga_url/stores/$store_id/write" \
        -H "Content-Type: application/json" \
        -d '{
  "writes": {"tuple_keys": [
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:git"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:jira"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:confluence"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:slack"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:gmail"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:gdrive"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:s3"},
    {"user": "shokan:shokanllm", "relation": "shokan", "object": "mcp_server:filesystem"}
  ]}
}' >/dev/null 2>&1 || true

    # Store the store ID in the K8s secret
    kubectl patch secret -n "$namespace" "shokanllm-secret" \
        -p "{\"data\":{\"openfga-store-id\":\"$(echo -n "$store_id" | base64)\"}}" >/dev/null

    echo "✅ OpenFGA store initialized: $store_id (model: ${model_id:-unknown})"
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
        if curl -sf "$url/realms/master" >/dev/null 2>&1; then
            echo "✅ Keycloak API is ready"
            return 0
        fi
        sleep 5
    done
    echo "❌ Keycloak API not ready after $timeout seconds"
    return 1
}

create_keycloak_admin_user() {
    # Human platform admin — forced password change on first browser login.
    # Does NOT have realm-admin (that belongs to the service account).
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local username="$3"
    local initial_password="$4"
    local realm="${5:-$SHOKAN_KC_REALM}"

    echo "👤 Creating human admin user '$username' in realm '$realm'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
    [[ -z "$token" ]] && { echo "❌ Could not obtain Keycloak admin token."; return 1; }

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$keycloak_url/admin/realms/$realm/users" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$username\",\"enabled\":true,\"emailVerified\":true,\"requiredActions\":[\"UPDATE_PASSWORD\"]}" 2>/dev/null)
    [[ "$http_status" != "201" && "$http_status" != "409" ]] && { echo "❌ Failed to create user (HTTP $http_status)"; return 1; }

    local user_id
    user_id=$(curl -sf "$keycloak_url/admin/realms/$realm/users?username=${username}&exact=true" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)
    [[ -z "$user_id" ]] && { echo "❌ Could not retrieve user ID for '$username'"; return 1; }

    # Set non-temporary password (temporary would block ROPC and is redundant with UPDATE_PASSWORD action)
    curl -sf -X PUT "$keycloak_url/admin/realms/$realm/users/$user_id/reset-password" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"password\",\"value\":\"${initial_password}\",\"temporary\":false}" >/dev/null

    # Keycloak 26 ignores requiredActions in the POST body — set them explicitly via PUT.
    curl -sf -X PUT "$keycloak_url/admin/realms/$realm/users/$user_id" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"requiredActions\":[\"UPDATE_PASSWORD\"]}" >/dev/null

    echo "✅ Human admin '$username' created (UPDATE_PASSWORD required on first browser login)"
}

_assign_realm_admin_role() {
    # Helper: grant realm-admin client role to a user.
    local keycloak_url="$1"
    local token="$2"
    local realm="$3"
    local user_id="$4"

    local rm_client_id
    rm_client_id=$(curl -sf "$keycloak_url/admin/realms/$realm/clients?clientId=realm-management" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)
    [[ -z "$rm_client_id" ]] && return 1

    local role_id
    role_id=$(curl -sf "$keycloak_url/admin/realms/$realm/clients/$rm_client_id/roles/realm-admin" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
    [[ -z "$role_id" ]] && return 1

    curl -sf -X POST \
        "$keycloak_url/admin/realms/$realm/users/$user_id/role-mappings/clients/$rm_client_id" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "[{\"id\":\"$role_id\",\"name\":\"realm-admin\"}]" >/dev/null 2>&1 || true
}

create_keycloak_service_account() {
    # Machine user for Keycloak admin API calls from the pod.
    # Stable password, no required actions, realm-admin role.
    local keycloak_url="$1"
    local bootstrap_password="$2"
    local username="$3"
    local svc_password="$4"
    local namespace="$5"
    local secret_key="$6"
    local realm="${7:-$SHOKAN_KC_REALM}"

    echo "🤖 Creating Keycloak service account '$username' in realm '$realm'..."

    local token
    token=$(curl -sf -X POST "$keycloak_url/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli&username=admin&password=${bootstrap_password}&grant_type=password" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null)
    [[ -z "$token" ]] && { echo "❌ Could not obtain Keycloak admin token."; return 1; }

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$keycloak_url/admin/realms/$realm/users" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$username\",\"enabled\":true,\"emailVerified\":true,\"email\":\"${username}@shokan.internal\",\"firstName\":\"Shokan\",\"lastName\":\"Service\"}" 2>/dev/null)
    [[ "$http_status" != "201" && "$http_status" != "409" ]] && { echo "❌ Failed to create service account (HTTP $http_status)"; return 1; }

    local user_id
    user_id=$(curl -sf "$keycloak_url/admin/realms/$realm/users?username=${username}&exact=true" \
        -H "Authorization: Bearer $token" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['id']) if d else exit(1)" 2>/dev/null)
    [[ -z "$user_id" ]] && { echo "❌ Could not retrieve user ID for '$username'"; return 1; }

    curl -sf -X PUT "$keycloak_url/admin/realms/$realm/users/$user_id/reset-password" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"type\":\"password\",\"value\":\"${svc_password}\",\"temporary\":false}" >/dev/null

    _assign_realm_admin_role "$keycloak_url" "$token" "$realm" "$user_id"

    kubectl patch secret -n "$namespace" "shokanllm-secret" \
        -p "{\"data\":{\"${secret_key}\":\"$(echo -n "$svc_password" | base64)\"}}" >/dev/null

    echo "✅ Service account '$username' created with realm-admin role (secret key: $secret_key)"
}

detect_node_ram_gb() {
    # Returns total allocatable RAM in GB across all schedulable nodes.
    kubectl get nodes -o json 2>/dev/null \
        | python3 -c "
import sys, json
data = json.load(sys.stdin)
total = 0.0
for node in data.get('items', []):
    taints = node.get('spec', {}).get('taints') or []
    if any(t.get('effect') == 'NoSchedule' for t in taints):
        continue
    mem = (node.get('status', {}).get('allocatable') or {}).get('memory', '0Ki')
    for suffix, factor in [('Ki', 2**10), ('Mi', 2**20), ('Gi', 2**30)]:
        if mem.endswith(suffix):
            total += int(mem[:-len(suffix)]) * factor / 1024**3
            break
    else:
        try:
            total += int(mem) / 1024**3
        except Exception:
            pass
print(round(total, 1))
" 2>/dev/null || echo "0"
}

select_default_model() {
    # Maps total cluster RAM (GB) to the largest model that fits comfortably.
    # Headroom factor ~1.15×: qwen2.5:0.5b ≈ 0.5 GB, llama3.2:1b ≈ 1.5 GB, llama3.2:3b ≈ 2.3 GB.
    local ram_gb="$1"
    if awk "BEGIN { exit ($ram_gb >= 16) ? 0 : 1 }"; then
        echo "llama3.2:3b"
    elif awk "BEGIN { exit ($ram_gb >= 8) ? 0 : 1 }"; then
        echo "llama3.2:1b"
    else
        echo "qwen2.5:0.5b"
    fi
}

pull_default_ollama_model() {
    local namespace="$1"
    local model="$2"

    echo "📥 Pulling default Ollama model: $model (this may take several minutes)..."

    local ollama_pod
    ollama_pod=$(kubectl get pods -n "$namespace" -l app.kubernetes.io/name=ollama \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [[ -z "$ollama_pod" ]] && { echo "❌ Ollama pod not found"; return 1; }

    if kubectl exec -n "$namespace" "$ollama_pod" -- ollama pull "$model"; then
        echo "✅ Model '$model' pulled successfully"
    else
        echo "❌ Failed to pull model '$model' — installation continues without it"
        return 0
    fi
}

pull_system_ollama_models() {
    local namespace="$1"

    # Initialise system-models key if absent
    local existing
    existing=$(kubectl get secret shokanllm-secret -n "$namespace" \
        -o jsonpath='{.data.system-models}' 2>/dev/null | base64 --decode 2>/dev/null || true)
    if [[ -z "$existing" ]]; then
        kubectl patch secret -n "$namespace" "shokanllm-secret" \
            -p "{\"data\":{\"system-models\":\"$(echo -n '["nomic-embed-text"]' | base64)\"}}" >/dev/null
        existing='["nomic-embed-text"]'
    fi

    local ollama_pod
    ollama_pod=$(kubectl get pods -n "$namespace" -l app.kubernetes.io/name=ollama \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    [[ -z "$ollama_pod" ]] && { echo "❌ Ollama pod not found — skipping system model pull"; return 0; }

    local models
    models=$(echo "$existing" | python3 -c "import sys,json; [print(m) for m in json.load(sys.stdin)]" 2>/dev/null)
    for model in $models; do
        echo "📥 Pulling system model: $model..."
        if kubectl exec -n "$namespace" "$ollama_pod" -- ollama pull "$model"; then
            echo "✅ System model '$model' pulled"
        else
            echo "⚠️  Failed to pull system model '$model' — installation continues"
        fi
    done
}

register_model_in_litellm() {
    local litellm_url="$1"
    local model_alias="$2"    # alias exposed by proxy, e.g. "llama3.2:1b"
    local model_string="$3"   # litellm model string, e.g. "ollama/llama3.2:1b"
    local master_key="$4"

    echo "📝 Registering model '$model_alias' in LiteLLM..."

    local http_status
    http_status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$litellm_url/model/new" \
        -H "Authorization: Bearer $master_key" \
        -H "Content-Type: application/json" \
        -d "{\"model_name\":\"$model_alias\",\"litellm_params\":{\"model\":\"$model_string\"}}")

    if [[ "$http_status" == "200" || "$http_status" == "201" ]]; then
        echo "✅ Model '$model_alias' registered in LiteLLM"
    else
        echo "⚠️  LiteLLM model registration HTTP $http_status — model may already be registered"
    fi
}

register_model_in_openfga() {
    local openfga_url="$1"
    local store_id="$2"
    local model_id="$3"   # e.g. "ollama/llama3.2:1b"

    echo "🔓 Registering model '$model_id' in OpenFGA..."

    curl -sf -X POST "$openfga_url/stores/$store_id/write" \
        -H "Content-Type: application/json" \
        -d "{\"writes\":{\"tuple_keys\":[{\"user\":\"shokan:shokanllm\",\"relation\":\"shokan\",\"object\":\"llm_model:$model_id\"}]}}" \
        >/dev/null 2>&1 || true

    echo "✅ Model '$model_id' registered in OpenFGA"
}

build_core_image() {
    local context_dir="$1"   # path to core/
    local image_tag="${2:-shokan-core:latest}"
    local current_context
    current_context=$(kubectl config current-context 2>/dev/null || echo "unknown")

    echo "🔨 Building Shokan Core image: $image_tag"

    if [[ "$current_context" == "minikube" ]]; then
        if ! minikube image build -t "$image_tag" "$context_dir"; then
            echo "❌ Docker build failed for $image_tag"
            exit 1
        fi
    else
        if ! docker build -t "$image_tag" "$context_dir"; then
            echo "❌ Docker build failed for $image_tag"
            exit 1
        fi
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

    if ! helm lint "$chart_path" 2>&1; then
        echo "❌ Helm chart validation failed for $chart_path"
        exit 1
    fi

    local helm_out
    if ! helm_out=$(helm upgrade --install "$release_name" "$chart_path" \
        --namespace "$namespace" \
        ${extra_values:+$extra_values} 2>&1); then
        echo "❌ Failed to install Helm chart '$release_name':"
        echo "$helm_out"
        exit 1
    fi
    echo "✅ Helm chart '$release_name' installed successfully"
}

resolve_ingress_base_dns() {
    local context="$1"

    if [[ "$context" == "minikube" ]]; then
        # With the Docker driver on macOS the Minikube node IP (192.168.49.x) lives
        # inside Colima's VM and is not routable from the Mac host. When Minikube is
        # started with --ports=80:80,443:443 the ingress binds on the Mac's localhost
        # instead, so use 127.0.0.1 as the nip.io base on macOS.
        local driver
        driver=$(minikube profile list -o json 2>/dev/null \
            | python3 -c "import sys,json; p=json.load(sys.stdin); \
              print(next((c['Config']['Driver'] for c in p.get('valid',[]) \
              if c['Name']=='minikube'),''))" 2>/dev/null || echo "")
        if [[ "$(uname -s)" == "Darwin" && "$driver" == "docker" ]]; then
            echo "127.0.0.1.nip.io"
            return 0
        fi

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
    local name="$2"
    local timeout="${3:-300}"

    local resource_type
    if kubectl get deployment "$name" -n "$namespace" &>/dev/null; then
        resource_type="deployment"
    elif kubectl get statefulset "$name" -n "$namespace" &>/dev/null; then
        resource_type="statefulset"
    else
        echo "❌ No deployment or statefulset named '$name' found in namespace '$namespace'."
        exit 1
    fi

    echo "⏳ Waiting for $resource_type '$name' in namespace '$namespace' to be ready..."
    if ! kubectl rollout status "$resource_type/$name" -n "$namespace" --timeout="${timeout}s"; then
        echo "❌ $resource_type '$name' did not become ready within $timeout seconds."
        exit 1
    fi
    echo "✅ $resource_type '$name' is ready."
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
    local no_wait="${6:-false}"  # pass "true" for charts whose hooks conflict with --wait

    echo "📦 Installing Helm chart: $release_name from $repo_chart in namespace $namespace..."

    # Ensure helm repo is updated
    helm repo update &>/dev/null || true

    # Build install command
    local wait_flag="--wait --timeout \"$timeout\""
    [[ "$no_wait" == "true" ]] && wait_flag=""
    local install_cmd="helm upgrade --install \"$release_name\" \"$repo_chart\" --namespace \"$namespace\" $wait_flag"

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
    
    echo "🔍 Checking that database '$db_name' exists in PostgreSQL..."

    if _pg_psql "$namespace" -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" | grep -q "1"; then
        echo "✅ Database '$db_name' exists in PostgreSQL"
        return 0
    else
        echo "❌ Database '$db_name' does NOT exist in PostgreSQL"
        return 1
    fi
}

verify_all_databases() {
    local namespace="$1"
    local missing_dbs=0

    echo ""
    echo "📊 Verifying all required databases..."

    for db_name in "keycloak" "openfga" "litellm"; do
        if ! verify_database_exists "$namespace" "$db_name"; then
            ((missing_dbs++))
        fi
    done

    if [[ $missing_dbs -gt 0 ]]; then
        echo ""
        echo "⚠️  $missing_dbs required database(s) are missing"
        echo "💡 Run: bash $(dirname "$0")/init-databases.sh $namespace"
        return 1
    else
        echo ""
        echo "✅ All required databases exist"
        return 0
    fi
}

wait_for_database_ready() {
    local namespace="$1"
    local timeout="${2:-300}"

    echo "⏳ Waiting for PostgreSQL to be ready..."

    for ((i=0; i<timeout; i+=10)); do
        if _pg_psql "$namespace" -U postgres -d postgres -c "SELECT 1" &>/dev/null; then
            echo "✅ PostgreSQL is ready"
            return 0
        fi
        echo "   Retrying in 10 seconds... ($i/$timeout)"
        sleep 10
    done

    echo "❌ PostgreSQL not ready after $timeout seconds"
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
    local name="$2"
    local resource_type ready desired

    if kubectl get deployment "$name" -n "$namespace" &>/dev/null; then
        resource_type="deployment"
    elif kubectl get statefulset "$name" -n "$namespace" &>/dev/null; then
        resource_type="statefulset"
    else
        echo -e "${RED}❌${NC} $name: not found (no deployment or statefulset)"
        return
    fi

    ready=$(kubectl get "$resource_type" "$name" -n "$namespace" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    desired=$(kubectl get "$resource_type" "$name" -n "$namespace" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0")
    if [[ "$ready" -eq "$desired" ]] && [[ "$desired" -gt 0 ]]; then
        echo -e "${GREEN}✅${NC} $name ($resource_type): $ready/$desired replicas ready"
    else
        echo -e "${RED}❌${NC} $name ($resource_type): $ready/$desired replicas ready"
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
    if _pg_psql "$namespace" -U postgres -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$db_name'" 2>/dev/null | grep -q "1"; then
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
# PostgreSQL exec helpers (injects PGPASSWORD automatically)
# ============================================================

_pg_psql() {
    local namespace="$1"; shift
    local pg_pass
    pg_pass=$(get_db_password "db-postgres-password" "$namespace")
    kubectl exec statefulset/postgresql -n "$namespace" -- \
        env PGPASSWORD="$pg_pass" psql "$@"
}

_pg_psql_i() {
    local namespace="$1"; shift
    local pg_pass
    pg_pass=$(get_db_password "db-postgres-password" "$namespace")
    kubectl exec -i statefulset/postgresql -n "$namespace" -- \
        env PGPASSWORD="$pg_pass" psql "$@"
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

    echo "🗄️  Initializing PostgreSQL databases..."
    echo ""
    echo "⏳ Waiting for PostgreSQL..."
    kubectl rollout status statefulset/postgresql -n "$namespace" --timeout=300s

    echo "📝 Running initialization SQL..."
    _pg_psql_i "$namespace" -U postgres -d postgres \
        -v "kc_pass=$kc_pass" \
        -v "fga_pass=$fga_pass" \
        -v "ltm_pass=$ltm_pass" <<'EOSQL'
-- Databases (idempotent via \gexec)
SELECT 'CREATE DATABASE keycloak WITH OWNER postgres ENCODING ''UTF8'' LC_COLLATE ''C'' LC_CTYPE ''C'' TEMPLATE template0'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'keycloak')\gexec
SELECT 'CREATE DATABASE openfga WITH OWNER postgres ENCODING ''UTF8'''
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'openfga')\gexec
SELECT 'CREATE DATABASE litellm WITH OWNER postgres ENCODING ''UTF8'''
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'litellm')\gexec

-- Roles (CREATE if not exists, then always set password)
SELECT 'CREATE USER keycloak'
  WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'keycloak')\gexec
ALTER ROLE keycloak WITH LOGIN PASSWORD :'kc_pass';
GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;

SELECT 'CREATE USER openfga'
  WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'openfga')\gexec
ALTER ROLE openfga WITH LOGIN PASSWORD :'fga_pass';
GRANT ALL PRIVILEGES ON DATABASE openfga TO openfga;

SELECT 'CREATE USER litellm'
  WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'litellm')\gexec
ALTER ROLE litellm WITH LOGIN PASSWORD :'ltm_pass';
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
    echo "✅ Databases and users initialized: keycloak  •  openfga  •  litellm"
}

# ============================================================
# Full installation verification
# ============================================================

verify_installation() {
    local namespace="${1:-shokanllm}"
    echo "🔍 Verifying Shokan-LLM installation in namespace: $namespace"
    echo ""
    echo "📦 Deployments:"
    check_deployment "$namespace" "qdrant"
    check_deployment "$namespace" "ollama"
    check_deployment "$namespace" "litellm"
    check_deployment "$namespace" "postgresql"
    check_deployment "$namespace" "keycloak"
    check_deployment "$namespace" "openfga"
    check_deployment "$namespace" "shokan-core"
    echo ""
    echo "🔌 Services:"
    check_service "$namespace" "qdrant" "6333"
    check_service "$namespace" "ollama" "11434"
    check_service "$namespace" "litellm" "8000"
    check_service "$namespace" "postgresql" "5432"
    check_service "$namespace" "keycloak" "8080"
    check_service "$namespace" "openfga" "8080"
    check_service "$namespace" "shokan-core" "7860"
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
    echo "   Choose a passphrase, or press Enter to generate one automatically."
    echo "   You will need it to restore credentials on a new cluster."
    echo ""

    local tmp_stderr
    tmp_stderr=$(mktemp)

    kubectl get secret shokanllm-secret -n "$namespace" -o yaml \
        | sed '/^\s*\(resourceVersion\|uid\|creationTimestamp\|generation\|selfLink\|managedFields\):/d' \
        | age --passphrase -o "$output_file" 2> >(tee "$tmp_stderr" >&2)

    chmod 600 "$output_file"

    # If age auto-generated the passphrase, extract and display it prominently
    local autogen_pass=""
    autogen_pass=$(grep "using autogenerated passphrase" "$tmp_stderr" \
        | sed 's/.*using autogenerated passphrase "\(.*\)".*/\1/' 2>/dev/null || true)
    rm -f "$tmp_stderr"

    echo ""
    echo "✅ Backup saved: $output_file"

    if [[ -n "$autogen_pass" ]]; then
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "⚠️  PASSPHRASE AUTOGENERADA — ANÓTALA AHORA"
        echo ""
        echo "   🔑 $autogen_pass"
        echo ""
        echo "   Without this passphrase you CANNOT restore the backup."
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    fi
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
    kubectl rollout status statefulset/postgresql -n "$namespace" --timeout=60s &>/dev/null
    _pg_psql "$namespace" -U postgres --no-password -c "" 2>/dev/null || true
    local pg_pass
    pg_pass=$(get_db_password "db-postgres-password" "$namespace")
    kubectl exec statefulset/postgresql -n "$namespace" -- \
        env PGPASSWORD="$pg_pass" pg_dumpall -U postgres | gzip > "$output_file"
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
    kubectl rollout status statefulset/postgresql -n "$namespace" --timeout=120s &>/dev/null
    gunzip -c "$input_file" | _pg_psql_i "$namespace" -U postgres -d postgres
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