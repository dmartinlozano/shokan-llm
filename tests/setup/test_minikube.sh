#!/bin/bash
# Smoke tests — verifies minikube-setup.sh result
# Requires: minikube already running

PASS=0; FAIL=0

assert() {
    local desc="$1" cmd="$2"
    if eval "$cmd" &>/dev/null; then
        echo "✅  $desc"; ((PASS++))
    else
        echo "❌  $desc"; ((FAIL++))
    fi
}

echo "▶  Minikube setup smoke tests"
echo ""

# ── Precondition: fail fast if minikube is not running ────────────────────────
if ! minikube status 2>/dev/null | grep -q 'host: Running'; then
    echo "⚠️  Minikube is not running."
    echo "   Start it first: bash minikube-setup.sh"
    echo ""
    exit 1
fi

# ── Runtime checks ────────────────────────────────────────────────────────────
assert "Docker is running" \
    "docker info"

assert "Minikube host is Running" \
    "minikube status | grep -q 'host: Running'"

assert "Minikube apiserver is Running" \
    "minikube status | grep -q 'apiserver: Running'"

assert "kubectl context is minikube" \
    "[[ \$(kubectl config current-context 2>/dev/null) == 'minikube' ]]"

assert "kubectl can reach the API server" \
    "kubectl cluster-info"

assert "Addon: ingress enabled" \
    "minikube addons list 2>/dev/null | grep -E 'ingress' | grep -v 'ingress-dns' | grep -q enabled"

assert "Addon: storage-provisioner enabled" \
    "minikube addons list 2>/dev/null | grep -E 'storage-provisioner' | grep -q enabled"

assert "Addon: default-storageclass enabled" \
    "minikube addons list 2>/dev/null | grep -E 'default-storageclass' | grep -q enabled"

assert "Default StorageClass exists" \
    "kubectl get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class==\"true\")].metadata.name}' | grep -q ."

assert "ingress-nginx pod is running" \
    "kubectl get pods -n ingress-nginx --field-selector=status.phase=Running 2>/dev/null | grep -q ingress-nginx-controller"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
