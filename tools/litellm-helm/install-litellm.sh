#!/bin/bash
set -e

# Quick LiteLLM install script using Helm
# Usage: ./install-litellm.sh [namespace] [release-name]

NAMESPACE="${1:-shokanllm}"
RELEASE_NAME="${2:-litellm}"
HELM_CHART_PATH="$(dirname "$0")/litellm-helm"

echo "🚀 Installing LiteLLM in namespace: $NAMESPACE"
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
echo "⚙️ Installing LiteLLM..."
helm upgrade --install "$RELEASE_NAME" "$HELM_CHART_PATH" \
    --namespace "$NAMESPACE" \
    --wait \
    --timeout 5m

echo ""
echo "✅ LiteLLM installed successfully!"
echo ""
echo "📊 Deployment status:"
kubectl get deployments -n "$NAMESPACE" -l "app.kubernetes.io/name=litellm"
echo ""
echo "🔗 To access LiteLLM:"
echo "   kubectl port-forward -n $NAMESPACE svc/litellm 8000:8000"
echo "   Then open: http://localhost:8000"
echo ""
echo "📝 View logs:"
echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=litellm -f"
echo ""
echo "📋 List models:"
echo "   curl http://localhost:8000/models"
echo ""
echo "💬 Send a chat request:"
echo "   curl -X POST http://localhost:8000/chat/completions \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"model\": \"ollama-mistral\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello!\"}]}'"
echo ""
echo "🗑️ To uninstall:"
echo "   helm uninstall $RELEASE_NAME -n $NAMESPACE"
