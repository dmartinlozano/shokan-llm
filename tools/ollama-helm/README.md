# Ollama Helm Chart

Helm chart para desplegar Ollama (motor LLM local) en Kubernetes.

## Descripción

[Ollama](https://ollama.ai) es una herramienta para ejecutar modelos de lenguaje grandes (LLM) localmente. Este chart de Helm simplifica su despliegue en Kubernetes.

## Prerrequisitos

- Kubernetes 1.19+
- Helm 3+
- Almacenamiento suficiente (mínimo 50Gi para modelos)

## Instalación

### Instalación básica

```bash
helm install ollama ./ollama-helm
```

### Instalación en namespace específico

```bash
helm install ollama ./ollama-helm --namespace shokanllm --create-namespace
```

### Instalación con GPU NVIDIA

```bash
helm install ollama ./ollama-helm \
  --set-string 'ollama.environment.OLLAMA_CUDA_COMPUTE_CAPABILITY=8.0' \
  --set resources.limits.nvidia\.com/gpu=1 \
  --set resources.requests.nvidia\.com/gpu=1
```

### Instalación con valores personalizados

```bash
helm install ollama ./ollama-helm -f custom-values.yaml
```

## Desinstalación

```bash
helm uninstall ollama
```

## Parámetros

### Imagen

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `image.repository` | `ollama/ollama` | Repositorio de la imagen |
| `image.tag` | `latest` | Etiqueta de la imagen |
| `image.pullPolicy` | `IfNotPresent` | Política de extracción de imagen |

### Replicación y escalabilidad

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `replicaCount` | `1` | Número de réplicas (generalmente 1 para LLM) |
| `autoscaling.enabled` | `false` | Habilitar escalado automático |

### Servicio

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `service.type` | `ClusterIP` | Tipo de servicio |
| `service.port` | `11434` | Puerto del servicio |

### Persistencia

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `persistence.enabled` | `true` | Habilitar persistencia para modelos |
| `persistence.storageClassName` | `standard` | Clase de almacenamiento |
| `persistence.size` | `50Gi` | Tamaño del almacenamiento (aumentar si necesitas más modelos) |
| `persistence.mountPath` | `/root/.ollama` | Ruta de montaje en el contenedor |

### Recursos

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `resources.requests.cpu` | `2000m` | CPU solicitada |
| `resources.requests.memory` | `4Gi` | Memoria solicitada |
| `resources.limits.cpu` | `4000m` | Límite de CPU |
| `resources.limits.memory` | `8Gi` | Límite de memoria |

### Ollama

| Parámetro | Por defecto | Descripción |
| --- | --- | --- |
| `ollama.models` | `["mistral"]` | Lista de modelos a descargar |
| `ollama.environment.OLLAMA_HOST` | `0.0.0.0:11434` | Host y puerto de escucha |

## Ejemplos de uso

### Instalación con almacenamiento más grande

```bash
helm install ollama ./ollama-helm \
  --set persistence.size=100Gi
```

### Instalación con múltiples modelos preinstalados

```bash
helm install ollama ./ollama-helm \
  --set 'ollama.models[0]=mistral' \
  --set 'ollama.models[1]=neural-chat' \
  --set 'ollama.models[2]=llama2'
```

### Instalación con más recursos

```bash
helm install ollama ./ollama-helm \
  --set resources.requests.memory=16Gi \
  --set resources.limits.memory=24Gi \
  --set resources.limits.cpu=8000m
```

### Instalación en nodos con GPU

```bash
helm install ollama ./ollama-helm \
  --set nodeSelector.gpu=true \
  --set 'ollama.environment.OLLAMA_NUM_GPU=1'
```

## Acceso a Ollama

### Usando port-forward

```bash
kubectl port-forward svc/ollama 11434:11434
```

Luego accede a `http://localhost:11434`

### Desde dentro del clúster

```
http://ollama:11434
```

### Listar modelos disponibles

```bash
curl http://localhost:11434/api/tags
```

### Descargar un modelo

```bash
curl -X POST http://localhost:11434/api/pull -d '{"name":"mistral"}'
```

### Generar texto

```bash
curl -X POST http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistral",
    "prompt": "Why is the sky blue?",
    "stream": false
  }'
```

## Monitoreo

### Ver logs

```bash
kubectl logs -f deployment/ollama
```

### Ver estado

```bash
kubectl get pods -l app.kubernetes.io/name=ollama
```

### Ver eventos

```bash
kubectl describe deployment ollama
```

### Verificar modelos descargados

```bash
kubectl exec -it deployment/ollama -- ollama list
```

## Resolución de problemas

### Pod no inicia

1. Verifique los logs: `kubectl logs deployment/ollama`
2. Verifique los eventos: `kubectl describe pod <pod-name>`
3. Verifique los recursos disponibles: `kubectl top nodes`

### Problema de almacenamiento

1. Verifique el PVC: `kubectl get pvc`
2. Verifique el estado del PVC: `kubectl describe pvc ollama`
3. Verifique que la clase de almacenamiento existe: `kubectl get storageclass`

### Modelos no se descargan automáticamente

Los modelos en `ollama.models` se descargan durante el arranque del contenedor. Si fallan:

1. Verifique los logs: `kubectl logs deployment/ollama`
2. Descargue manualmente: `kubectl exec -it deployment/ollama -- ollama pull mistral`

### GPU no se detecta

1. Verifique que NVIDIA Container Runtime está instalado
2. Verifique que los nodos tienen GPUs: `kubectl describe nodes | grep nvidia`
3. Asigne GPUs correctamente en values.yaml

## Seguridad

- El contenedor se ejecuta como root (necesario para GPU y hardware)
- Se aplica una política de seguridad de contenedor

## Performance Tips

1. **Memoria:** Asegúrate de que cada contenedor tiene suficiente RAM. Mistral necesita ~8GB
2. **Storage:** Usa SSD para mejor performance. HDD será muy lento
3. **GPU:** Si tienes GPUs, úsalas para acceleration significativa
4. **CPU:** CPU matters para máquinas sin GPU. Usa al menos 2-4 cores
5. **Réplicas:** Generalmente mantén 1 réplica para evitar issues de shared storage

## Licencia

MIT

## Contribuciones

Las contribuciones son bienvenidas. Por favor, envíe pull requests con mejoras.

## Referencias

- [Ollama GitHub](https://github.com/ollama/ollama)
- [Ollama Models](https://ollama.ai/library)
- [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md)
