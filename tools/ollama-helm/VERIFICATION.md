# Verificación de Ollama

Guía para verificar que Ollama está funcionando correctamente en Kubernetes.

## 1. Verificar que el pod está corriendo

```bash
kubectl get pods -n shokanllm -l app.kubernetes.io/name=ollama
```

Deberías ver un pod en estado `Running`.

## 2. Verificar los logs

```bash
kubectl logs -n shokanllm -l app.kubernetes.io/name=ollama -f
```

Deberías ver logs indicando que el servidor está escuchando en el puerto 11434.

## 3. Verificar el servicio

```bash
kubectl get svc -n shokanllm ollama
```

Deberías ver el servicio con ClusterIP asignado.

## 4. Acceder a la API REST

### Opción A: Usar port-forward

```bash
kubectl port-forward -n shokanllm svc/ollama 11434:11434
```

Luego en otra terminal:

```bash
curl http://localhost:11434/api/tags
```

### Opción B: Ejecutar desde dentro del pod

```bash
kubectl exec -it -n shokanllm deployment/ollama -- curl http://localhost:11434/api/tags
```

Respuesta esperada (lista de modelos):
```json
{
  "models": [
    {
      "name": "mistral:latest",
      "modified_at": "2024-01-15T10:30:00Z",
      "size": 3819183447
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
kubectl top pod -n shokanllm -l app.kubernetes.io/name=ollama
```

Muestra el uso actual de CPU y memoria.

## 7. Listar modelos disponibles

```bash
kubectl exec -it -n shokanllm deployment/ollama -- ollama list
```

Deberías ver los modelos descargados.

## 8. Descargar un modelo

```bash
kubectl exec -it -n shokanllm deployment/ollama -- ollama pull neural-chat
```

Esto descargará el modelo neural-chat de Ollama library.

## 9. Generar texto desde el pod

```bash
kubectl exec -it -n shokanllm deployment/ollama -- ollama run mistral "Why is the sky blue?"
```

Deberías ver la respuesta del modelo.

## 10. Generar texto via API

```bash
curl -X POST http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral",
    "prompt": "Why is the sky blue?",
    "stream": false
  }'
```

## 11. Describir el deployment

```bash
kubectl describe deployment -n shokanllm ollama
```

Revisa los eventos para cualquier problema de programación.

## 12. Acceder a la terminal del pod

```bash
kubectl exec -it -n shokanllm deployment/ollama -- /bin/bash
```

Desde aquí puedes ejecutar comandos de Ollama directamente.

## 13. Monitoreo de uso

```bash
kubectl top pods -n shokanllm
kubectl top nodes
```

Verifica el uso de recursos en tiempo real.

## Troubleshooting

### El pod no inicia

1. Verifica los logs: `kubectl logs -n shokanllm deployment/ollama`
2. Verifica los eventos: `kubectl describe pod -n shokanllm <pod-name>`
3. Verifica el estado del nodo: `kubectl describe nodes`

### Error de almacenamiento

1. Verifica el PVC: `kubectl get pvc -n shokanllm`
2. Verifica la clase de almacenamiento: `kubectl get storageclass`
3. Verifica si hay espacio en el nodo: `df -h`

### Conexión rechazada

1. Verifica que el servicio existe: `kubectl get svc -n shokanllm`
2. Verifica que el pod está en estado Running: `kubectl get pods -n shokanllm`
3. Verifica los logs del pod: `kubectl logs -n shokanllm deployment/ollama`

### Modelos no descargan

1. Verifica conectividad de red: `kubectl exec -it -n shokanllm deployment/ollama -- curl -I https://ollama.ai`
2. Verifica espacio en disco: `kubectl exec -it -n shokanllm deployment/ollama -- df -h`
3. Verifica logs: `kubectl logs -n shokanllm deployment/ollama`

### GPU no se detecta

1. Verifica que NVIDIA Container Runtime está instalado
2. Verifica que los nodos tienen GPUs: `kubectl describe nodes | grep nvidia`
3. Asigna GPUs correctamente en values.yaml

## Documentación oficial

- [Ollama GitHub](https://github.com/ollama/ollama)
- [Ollama Models](https://ollama.ai/library)
- [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)

## Performance Tips

1. **CPU vs GPU:** Con CPU, Mistral tarda ~20-30 segundos. Con GPU (NVIDIA A100), ~1-2 segundos
2. **Memoria:** Mistral necesita ~8GB. Ensure enough memory available
3. **Storage:** Los modelos ocupan entre 3GB (Mistral) a 40GB (Llama2 70B)
4. **Network:** Los modelos se descargan desde internet, así que tener buena conexión es importante
