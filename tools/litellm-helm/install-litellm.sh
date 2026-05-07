#!/bin/bash
set -e

# Script de instalación rápida de LiteLLM con Helm
# Uso: ./install-litellm.sh [namespace] [release-name]

NAMESPACE="${1:-shokanllm}"
RELEASE_NAME="${2:-litellm}"
HELM_CHART_PATH="$(dirname "$0")/litellm-helm"

echo "🚀 Instalando LiteLLM en namespace: $NAMESPACE"
echo "📦 Release name: $RELEASE_NAME"
echo ""

# Verificar que Helm está instalado
if ! command -v helm &> /dev/null; then
    echo "❌ Helm no está instalado. Por favor instala Helm primero."
    exit 1
fi

# Verificar que kubectl está instalado
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl no está instalado. Por favor instala kubectl primero."
    exit 1
fi

# Crear namespace si no existe
echo "📁 Creando namespace si es necesario..."
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Validar el chart
echo "✓ Validando Helm chart..."
helm lint "$HELM_CHART_PATH"

# Instalar o actualizar el chart
echo "⚙️ Instalando LiteLLM..."
helm upgrade --install "$RELEASE_NAME" "$HELM_CHART_PATH" \
    --namespace "$NAMESPACE" \
    --wait \
    --timeout 5m

echo ""
echo "✅ ¡LiteLLM instalado exitosamente!"
echo ""
echo "📊 Status del despliegue:"
kubectl get deployments -n "$NAMESPACE" -l "app.kubernetes.io/name=litellm"
echo ""
echo "🔗 Para acceder a LiteLLM:"
echo "   kubectl port-forward -n $NAMESPACE svc/litellm 8000:8000"
echo "   Luego abre: http://localhost:8000"
echo ""
echo "📝 Ver logs:"
echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=litellm -f"
echo ""
echo "📋 Listar modelos:"
echo "   curl http://localhost:8000/models"
echo ""
echo "💬 Hacer una petición de chat:"
echo "   curl -X POST http://localhost:8000/chat/completions \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"model\": \"ollama-mistral\", \"messages\": [{\"role\": \"user\", \"content\": \"Hola!\"}]}'"
echo ""
echo "🗑️ Para desinstalar:"
echo "   helm uninstall $RELEASE_NAME -n $NAMESPACE"
