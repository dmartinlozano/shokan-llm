#!/bin/bash
set -e

# Quick Ollama install script using Helm
# Usage: ./install-ollama.sh [namespace] [release-name]

NAMESPACE="${1:-shokanllm}"
RELEASE_NAME="${2:-ollama}"
HELM_CHART_PATH="$(dirname "$0")/ollama-helm"

echo "🚀 Installing Ollama in namespace: $NAMESPACE"
echo "📦 Release name: $RELEASE_NAME"
echo ""

# Check Helm is installed
if ! command -v helm &> /dev/null; then
    echo "❌ Helm is not installed. Please install Helm first."
    exit 1
fi

# Check kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed. Please install kubectl first."
    exit 1
fi

# Create namespace if it does not exist
echo "📁 Creating namespace if needed..."
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Validate the chart
echo "✓ Validating Helm chart..."
helm lint "$HELM_CHART_PATH"

# Install or upgrade the chart
echo "⚙️ Installing Ollama..."
helm upgrade --install "$RELEASE_NAME" "$HELM_CHART_PATH" \
    --namespace "$NAMESPACE" \
    --wait \
    --timeout 5m

echo ""
echo "✅ Ollama installed successfully!"
echo ""
echo "📊 Deployment status:"
kubectl get deployments -n "$NAMESPACE" -l "app.kubernetes.io/name=ollama"
echo ""
echo "🔗 To access Ollama:"
echo "   kubectl port-forward -n $NAMESPACE svc/ollama 11434:11434"
echo "   Then open: http://localhost:11434"
echo ""
echo "📝 View logs:"
echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=ollama -f"
echo ""
echo "🤖 Pull a model:"
echo "   kubectl exec -it -n $NAMESPACE deployment/ollama -- ollama pull mistral"
echo ""
echo "📋 List models:"
echo "   kubectl exec -it -n $NAMESPACE deployment/ollama -- ollama list"
echo ""
echo "🗑️ To uninstall:"
echo "   helm uninstall $RELEASE_NAME -n $NAMESPACE"
