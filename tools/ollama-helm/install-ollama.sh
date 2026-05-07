#!/bin/bash
set -e

# Script de instalación rápida de Ollama con Helm
# Uso: ./install-ollama.sh [namespace] [release-name]

NAMESPACE="${1:-shokanllm}"
RELEASE_NAME="${2:-ollama}"
HELM_CHART_PATH="$(dirname "$0")/ollama-helm"

echo "🚀 Instalando Ollama en namespace: $NAMESPACE"
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
echo "⚙️ Instalando Ollama..."
helm upgrade --install "$RELEASE_NAME" "$HELM_CHART_PATH" \
    --namespace "$NAMESPACE" \
    --wait \
    --timeout 5m

echo ""
echo "✅ ¡Ollama instalado exitosamente!"
echo ""
echo "📊 Status del despliegue:"
kubectl get deployments -n "$NAMESPACE" -l "app.kubernetes.io/name=ollama"
echo ""
echo "🔗 Para acceder a Ollama:"
echo "   kubectl port-forward -n $NAMESPACE svc/ollama 11434:11434"
echo "   Luego abre: http://localhost:11434"
echo ""
echo "📝 Ver logs:"
echo "   kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=ollama -f"
echo ""
echo "🤖 Descargar un modelo:"
echo "   kubectl exec -it -n $NAMESPACE deployment/ollama -- ollama pull mistral"
echo ""
echo "📋 Listar modelos:"
echo "   kubectl exec -it -n $NAMESPACE deployment/ollama -- ollama list"
echo ""
echo "🗑️ Para desinstalar:"
echo "   helm uninstall $RELEASE_NAME -n $NAMESPACE"
