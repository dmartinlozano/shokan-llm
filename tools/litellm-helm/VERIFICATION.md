# Verificación de LiteLLM

Guía para verificar que LiteLLM está funcionando correctamente en Kubernetes.

## 1. Verificar que el pod está corriendo

```bash
kubectl get pods -n shokanllm -l app.kubernetes.io/name=litellm
```

Deberías ver un pod en estado `Running`.

## 2. Verificar los logs

```bash
kubectl logs -n shokanllm -l app.kubernetes.io/name=litellm -f
```

Deberías ver logs indicando que el proxy está activo y escuchando en puerto 8000.

## 3. Verificar el servicio

```bash
kubectl get svc -n shokanllm litellm
```

Deberías ver el servicio con ClusterIP asignado.

## 4. Acceder a la API REST

### Opción A: Usar port-forward

```bash
kubectl port-forward -n shokanllm svc/litellm 8000:8000
```

Luego en otra terminal:

```bash
curl http://localhost:8000/models
```

### Opción B: Ejecutar desde dentro del pod

```bash
kubectl exec -it -n shokanllm deployment/litellm -- curl http://localhost:8000/models
```

Respuesta esperada (lista de modelos):
```json
{
  "object": "list",
  "data": [
    {
      "id": "ollama-mistral",
      "object": "model",
      "owned_by": "openai-compatible"
    }
  ]
}
```

## 5. Ver el almacenamiento

```bash
kubectl get pvc -n shokanllm
```

Deberías ver el PVC en estado `Bound`.

## 6. Verificar recursos

```bash
kubectl top pod -n shokanllm -l app.kubernetes.io/name=litellm
```

Muestra el uso actual de CPU y memoria.

## 7. Verificar la configuración

```bash
kubectl get cm -n shokanllm litellm-config -o yaml
```

Deberías ver la configuración de modelos.

## 8. Hacer un request de chat con Ollama

```bash
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama-mistral",
    "messages": [
      {"role": "user", "content": "¿Cuál es la capital de Francia?"}
    ]
  }'
```

Respuesta esperada:
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "ollama-mistral",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "La capital de Francia es París."
      },
      "finish_reason": "stop"
    }
  ]
}
```

## 9. Hacer un request de chat con modelo remoto

```bash
# Primero, crear el secret con API keys
kubectl create secret generic litellm-keys \
  --from-literal=openai-key=$OPENAI_API_KEY \
  -n shokanllm

# Luego hacer la petición
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "user", "content": "Hello!"}
    ]
  }'
```

## 10. Describir el deployment

```bash
kubectl describe deployment -n shokanllm litellm
```

Revisa los eventos para cualquier problema de programación.

## 11. Acceder a los logs de LiteLLM

```bash
# Ver los últimos 100 líneas
kubectl logs -n shokanllm deployment/litellm --tail=100

# Ver logs seguidos
kubectl logs -n shokanllm deployment/litellm -f

# Ver logs incluyendo eventos
kubectl logs -n shokanllm deployment/litellm --timestamps=true
```

## 12. Testear diferentes modelos

```bash
# Listar todos los modelos disponibles
curl http://localhost:8000/models

# Testear completions de texto
curl -X POST http://localhost:8000/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama-mistral",
    "prompt": "The capital of France is",
    "max_tokens": 10
  }'
```

## 13. Health checks

```bash
# Liveness probe
curl http://localhost:8000/health/liveliness

# Readiness probe
curl http://localhost:8000/health/readiness
```

## 14. Estadísticas y métricas

```bash
# Ver uso de recursos
kubectl top pods -n shokanllm
kubectl top nodes

# Ver eventos del deployment
kubectl get events -n shokanllm
```

## Troubleshooting

### El pod no inicia

1. Verifica los logs: `kubectl logs -n shokanllm deployment/litellm`
2. Verifica los eventos: `kubectl describe pod -n shokanllm <pod-name>`
3. Verifica los recursos disponibles: `kubectl top nodes`

### Error conectando a Ollama

1. Verifica que Ollama está corriendo: `kubectl get pods -n shokanllm -l app.kubernetes.io/name=ollama`
2. Verifica la conectividad: `kubectl exec -it -n shokanllm deployment/litellm -- curl http://ollama:11434/api/tags`
3. Verifica la configuración del servicio: `kubectl get svc -n shokanllm ollama`

### Error con API keys

1. Verifica los secretos: `kubectl get secrets -n shokanllm`
2. Verifica que las variables de entorno están configuradas: `kubectl exec -it -n shokanllm deployment/litellm -- env | grep -i key`
3. Verifica los logs para errores de autenticación

### Problema de almacenamiento

1. Verifica el PVC: `kubectl get pvc -n shokanllm`
2. Verifica el estado del PVC: `kubectl describe pvc -n shokanllm litellm`
3. Verifica que la clase de almacenamiento existe: `kubectl get storageclass`

### Problemas de configuración

1. Verifica la ConfigMap: `kubectl get cm -n shokanllm litellm-config -o yaml`
2. Verifica que la configuración es válida YAML
3. Recrea el deployment: `kubectl rollout restart deployment/litellm -n shokanllm`

## Performance Monitoring

### Monitorear en tiempo real

```bash
# Ver uso de CPU y memoria continuamente
watch -n 1 'kubectl top pods -n shokanllm -l app.kubernetes.io/name=litellm'
```

### Analizar latencia

```bash
# Medir tiempo de respuesta
time curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "ollama-mistral", "messages": [{"role": "user", "content": "Hi"}]}'
```

## Integración con Ollama

Si tienes Ollama en el mismo cluster:

```bash
# Ver estado de Ollama
kubectl get pods -n shokanllm -l app.kubernetes.io/name=ollama

# Verificar conectividad desde LiteLLM
kubectl exec -it -n shokanllm deployment/litellm -- \
  curl http://ollama:11434/api/tags

# Hacer una petición pasando por LiteLLM
curl -X POST http://localhost:8000/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ollama-mistral",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Documentación oficial

- [LiteLLM GitHub](https://github.com/BerriAI/litellm)
- [LiteLLM Documentación](https://docs.litellm.ai/)
- [LiteLLM API Reference](https://docs.litellm.ai/docs/completion/api_reference)
- [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start)
