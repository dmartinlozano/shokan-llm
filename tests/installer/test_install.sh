#!/bin/bash
# Smoke tests — verifies installer.sh result
# Requires: full installation completed in namespace shokanllm

NAMESPACE="shokanllm"
PASS=0; FAIL=0

assert() {
    local desc="$1" cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "✅  $desc"; ((PASS++))
    else
        echo "❌  $desc"; ((FAIL++))
    fi
}

echo "▶  Installer smoke tests (namespace: $NAMESPACE)"
echo ""

# ── Deployments ───────────────────────────────────────────────────────────────
echo "Deployments:"
for dep in qdrant ollama litellm postgresql keycloak openfga shokan-core; do
    assert "  $dep is ready" \
        "[[ \$(kubectl get deployment $dep -n $NAMESPACE -o jsonpath='{.status.readyReplicas}' 2>/dev/null) -ge 1 ]]"
done

echo ""

# ── K8s secret keys ───────────────────────────────────────────────────────────
echo "Secret keys (shokanllm-secret):"
for key in \
    db-postgres-password \
    db-keycloak-password \
    db-openfga-password \
    db-litellm-password \
    keycloak-admin-password \
    shokan-admin-temp-password \
    oidc-client-secret-shokan-core \
    chainlit-auth-secret; do
    assert "  $key" \
        "kubectl get secret shokanllm-secret -n $NAMESPACE \
            -o jsonpath=\"{.data.${key}}\" 2>/dev/null | grep -q ."
done

echo ""

# ── Databases ─────────────────────────────────────────────────────────────────
echo "PostgreSQL databases:"
for db in keycloak openfga litellm; do
    assert "  database '$db' exists" \
        "kubectl exec deployment/postgresql -n $NAMESPACE -- \
            psql -U postgres -tc \"SELECT 1 FROM pg_database WHERE datname='$db'\" 2>/dev/null | grep -q 1"
done

echo ""

# ── Ingress ───────────────────────────────────────────────────────────────────
echo "Ingress:"
assert "  keycloak ingress exists" \
    "kubectl get ingress keycloak -n $NAMESPACE"
assert "  shokan-core ingress exists" \
    "kubectl get ingress shokan-core -n $NAMESPACE"

echo ""

# ── Keycloak API ──────────────────────────────────────────────────────────────
echo "Keycloak:"
KC_PORT=28080
kubectl port-forward -n "$NAMESPACE" svc/keycloak "$KC_PORT:8080" &>/dev/null &
KC_PF_PID=$!
sleep 5

assert "  health/ready responds 200" \
    "curl -sf http://localhost:$KC_PORT/health/ready"
assert "  shokan-admin user exists" \
    "curl -sf -X POST http://localhost:$KC_PORT/realms/master/protocol/openid-connect/token \
        -d 'client_id=admin-cli&grant_type=password&username=shokan-admin&password=\$(kubectl get secret shokanllm-secret -n $NAMESPACE -o jsonpath=\"{.data.shokan-admin-temp-password}\" | base64 --decode)' \
        | python3 -c \"import sys,json; print(json.load(sys.stdin).get('access_token',''))\" | grep -q ."
assert "  shokan-core OIDC client exists" \
    "curl -sf -X POST http://localhost:$KC_PORT/realms/master/protocol/openid-connect/token \
        -d 'client_id=admin-cli&grant_type=password&username=admin&password=\$(kubectl get secret shokanllm-secret -n $NAMESPACE -o jsonpath=\"{.data.keycloak-admin-password}\" | base64 --decode)' \
        | python3 -c \"import sys,json; t=json.load(sys.stdin)['access_token']; print(t)\" \
        | xargs -I{} curl -sf http://localhost:$KC_PORT/admin/realms/master/clients?clientId=shokan-core \
            -H 'Authorization: Bearer {}' \
        | python3 -c \"import sys,json; d=json.load(sys.stdin); exit(0 if d else 1)\""

kill $KC_PF_PID 2>/dev/null || true

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
