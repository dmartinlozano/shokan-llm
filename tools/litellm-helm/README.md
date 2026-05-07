# LiteLLM Helm Chart

Helm chart para desplegar LiteLLM (proxy unificado de LLMs) en Kubernetes.

## Descripción

[LiteLLM](https://github.com/BerriAI/litellm) es un proxy unificado que permite acceder a múltiples modelos de lenguaje (OpenAI, Claude, Ollama, etc.) a través de una API compatible con OpenAI. Este chart de Helm simplifica su despliegue en Kubernetes.

## Prerrequisitos

- Kubernetes 1.19+
- Helm 3+

## Instalación

### Instalación básica

```bash
helm install litellm ./litellm-helm
```

### Instalación en namespace específico

```bash
helm install litellm ./litellm-helm --namespace shokanllm --create-namespace
```

### Instalación con Ollama local

```bash
helm install litellm ./litellm-helm \
  --set 'configMap.litellm_config_yaml.model_list[0].litellm_params.api_base=http://ollama:11434'
```

### Instalación con valores personalizados

```bash
helm install litellm ./litellm-helm -f custom-values.yaml
```

## Desinstalación

```bash
helm uninstall litellm
```

## Parámetros

### Imagen

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `image.repository` | `ghcr.io/berriai/litellm` | Repositorio de la imagen |
| `image.tag` | `latest` | Etiqueta de la imagen |
| `image.pullPolicy` | `IfNotPresent` | Política de extracción de imagen |

### Replicación y escalabilidad

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `replicaCount` | `1` | Número de réplicas |
| `autoscaling.enabled` | `false` | Habilitar escalado automático |
| `autoscaling.minReplicas` | `1` | Réplicas mínimas |
| `autoscaling.maxReplicas` | `3` | Réplicas máximas |

### Servicio

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `service.type` | `ClusterIP` | Tipo de servicio |
| `service.port` | `8000` | Puerto del servicio |

### Persistencia

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `persistence.enabled` | `true` | Habilitar persistencia |
| `persistence.storageClassName` | `standard` | Clase de almacenamiento |
| `persistence.size` | `5Gi` | Tamaño del almacenamiento |

### Recursos

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `resources.requests.cpu` | `500m` | CPU solicitada |
| `resources.requests.memory` | `512Mi` | Memoria solicitada |
| `resources.limits.cpu` | `2000m` | Límite de CPU |
| `resources.limits.memory` | `2Gi` | Límite de memoria |

### LiteLLM

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `litellm.port` | `8000` | Puerto de escucha |
| `litellm.proxyEnabled` | `true` | Habilitar proxy |
| `litellm.logging.level` | `INFO` | Nivel de logging |

## Ejemplos de uso

### Instalación con base de datos PostgreSQL

```bash
helm install litellm ./litellm-helm \
  --set litellm.database.enabled=true \
  --set 'litellm.database.url=postgresql://user:password@postgres:5432/litellm'
```

### Instalación con escalado automático

```bash
helm install litellm ./litellm-helm \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=2 \
  --set autoscaling.maxReplicas=5
```

### Instalación con LoadBalancer

```bash
helm install litellm ./litellm-helm \
  --set service.type=LoadBalancer
```

## Configuración de Modelos

LiteLLM permite configurar múltiples modelos. Edita el `values.yaml` para agregar modelos:

### Modelos locales (Ollama)

```yaml
litellm:
  models:
    - model_name: "ollama-mistral"
      litellm_params:
        model: "ollama/mistral"
        api_base: "http://ollama:11434"
```

### Modelos remotos (OpenAI, Claude, etc.)

```yaml
litellm:
  models:
    - model_name: "gpt-4"
      litellm_params:
        model: "gpt-4"
    - model_name: "claude-3"
      litellm_params:
        model: "claude-3-opus-20240229"
```

## Gestión de API Keys

### Crear secretos para API keys

```bash
kubectl create secret generic litellm-keys \
  --from-literal=openai-key=$OPENAI_API_KEY \
  --from-literal=anthropic-key=$ANTHROPIC_API_KEY \
  -n shokanllm
```

### Referenciar en values.yaml

```yaml
env:
  - name: OPENAI_API_KEY
    valueFrom:
      secretKeyRef:
        name: litellm-keys
        key: openai-key
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: litellm-keys
        key: anthropic-key
```

## Acceso a LiteLLM

### Usando port-forward

```bash
kubectl port-forward svc/litellm 8000:8000
```

Luego accede a `http://localhost:8000`

### Desde dentro del clúster

```
http://litellm:8000
```

### Listar modelos disponibles

```bash
curl http://localhost:8000/models
```

### Hacer un request de chat

```bash
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama-mistral",
    "messages": [
      {"role": "user", "content": "Hola, ¿cómo estás?"}
    ]
  }'
```

## Monitoreo

### Ver logs

```bash
kubectl logs -f deployment/litellm
```

### Ver estado

```bash
kubectl get pods -l app.kubernetes.io/name=litellm
```

### Ver eventos

```bash
kubectl describe deployment litellm
```

### Verificar configuración

```bash
kubectl get cm litellm-config -o yaml
```

## Resolución de problemas

### Pod no inicia

1. Verifica los logs: `kubectl logs deployment/litellm`
2. Verifica los eventos: `kubectl describe pod <pod-name>`
3. Verifica los recursos disponibles: `kubectl top nodes`

### Problema de almacenamiento

1. Verifica el PVC: `kubectl get pvc`
2. Verifica el estado del PVC: `kubectl describe pvc litellm`
3. Verifica que la clase de almacenamiento existe: `kubectl get storageclass`

### Error conectando a modelos

1. Verifica que Ollama está disponible: `kubectl get svc ollama`
2. Verifica conectividad: `kubectl exec -it deployment/litellm -- curl http://ollama:11434/api/tags`
3. Verifica la configuración: `kubectl get cm litellm-config -o yaml`

### Problemas con API keys

1. Verifica los secretos: `kubectl get secrets`
2. Verifica que las variables de entorno están configuradas: `kubectl exec -it deployment/litellm -- env | grep API`
3. Verifica los logs para errores de autenticación

## Integración con Shokan-Core

Para usar LiteLLM en el core de Shokan:

```python
from litellm import completion

response = completion(
    model="ollama-mistral",
    api_base="http://litellm.shokanllm.svc.cluster.local:8000",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

O para un modelo remoto:

```python
response = completion(
    model="gpt-4",
    api_base="http://litellm.shokanllm.svc.cluster.local:8000",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

## Seguridad

- El contenedor se ejecuta como usuario no privilegiado (UID 1000)
- Se aplica una política de seguridad de contenedor restrictiva
- Las API keys se almacenan en Kubernetes Secrets

## Performance Tips

1. **Caching:** Usa persistencia para cachear respuestas
2. **Connection Pooling:** Configura pool sizes si usas BD
3. **Rate Limiting:** Considera agregar límites de tasa
4. **Monitoring:** Monitorea la latencia de los modelos
5. **Scaling:** Escala horizontalmente según la demanda

## Referencias

- [LiteLLM GitHub](https://github.com/BerriAI/litellm)
- [LiteLLM Documentación](https://docs.litellm.ai/)
- [LiteLLM Models](https://docs.litellm.ai/docs/providers)
- [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/user_keys)

## Licencia

MIT

## Contribuciones

Las contribuciones son bienvenidas. Por favor, envíe pull requests con mejoras.
