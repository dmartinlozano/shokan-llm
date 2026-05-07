# Advanced Configuration for Ollama

## GPU Configuration

### NVIDIA GPU

Para usar GPUs NVIDIA, primero asegúrate de que:

1. NVIDIA Container Runtime está instalado en los nodos
2. El driver NVIDIA está disponible

Luego, configura en `values.yaml`:

```yaml
resources:
  limits:
    nvidia.com/gpu: 1
  requests:
    nvidia.com/gpu: 1

ollama:
  environment:
    OLLAMA_NUM_GPU: "1"
    OLLAMA_CUDA_COMPUTE_CAPABILITY: "8.0"  # Ajusta según tu GPU
```

O usando Helm CLI:

```bash
helm install ollama ./ollama-helm \
  --set 'resources.limits.nvidia\.com/gpu=1' \
  --set 'resources.requests.nvidia\.com/gpu=1' \
  --set 'ollama.environment.OLLAMA_NUM_GPU=1'
```

## Model Preloading

### Descargar modelos durante la instalación

Aunque `ollama.models` está disponible en values.yaml, los modelos no se descargan automáticamente durante el startup. Para precargar modelos:

### Opción 1: Descargarlos manualmente después de la instalación

```bash
kubectl exec -it deployment/ollama -- ollama pull mistral
kubectl exec -it deployment/ollama -- ollama pull neural-chat
kubectl exec -it deployment/ollama -- ollama pull llama2
```

### Opción 2: Usar un InitContainer (avanzado)

Modifica el `deployment.yaml` para agregar un initContainer que descargue modelos:

```yaml
initContainers:
  - name: model-downloader
    image: ollama/ollama:latest
    command: ["sh", "-c", "ollama pull mistral && ollama pull neural-chat"]
    volumeMounts:
      - name: models
        mountPath: /root/.ollama
```

## Multi-Model Setup

### Ejecutar múltiples instancias de Ollama

Si necesitas diferentes modelos en diferentes instancias:

```bash
# Instancia 1 con Mistral
helm install ollama-mistral ./ollama-helm \
  -f values.yaml \
  -n shokanllm

# Instancia 2 con Llama2
helm install ollama-llama2 ./ollama-helm \
  -f values.yaml \
  --set fullnameOverride=ollama-llama2 \
  --set persistence.size=80Gi \
  -n shokanllm
```

## Performance Tuning

### Aumentar threads CPU

```bash
helm install ollama ./ollama-helm \
  --set 'ollama.environment.OLLAMA_NUM_PARALLEL=4'
```

### Reducir uso de memoria

```bash
helm install ollama ./ollama-helm \
  --set 'ollama.environment.OLLAMA_NUM_THREAD=2'
```

## Networking

### Acceso externo con LoadBalancer

```bash
helm install ollama ./ollama-helm \
  --set service.type=LoadBalancer
```

### Acceso externo con Ingress

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: ollama.example.com
      paths:
        - path: /
          pathType: Prefix
```

## Monitoring and Logging

### Logs en tiempo real

```bash
kubectl logs -f deployment/ollama --all-containers=true
```

### Acceder a historial de comandos

```bash
kubectl exec -it deployment/ollama -- bash
# Dentro del contenedor:
history
cat /root/.bash_history
```

## Security

### Network Policies

Para restringir el acceso a Ollama:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ollama-network-policy
  namespace: shokanllm
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: ollama
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              role: client
      ports:
        - protocol: TCP
          port: 11434
```

### Pod Security Policy (PSP)

```yaml
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: ollama-psp
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - 'configMap'
    - 'emptyDir'
    - 'projected'
    - 'secret'
    - 'downwardAPI'
    - 'persistentVolumeClaim'
  hostNetwork: false
  hostIPC: false
  hostPID: false
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'MustRunAs'
```

## Scaling

### Horizontal Scaling con shared storage

```bash
helm install ollama ./ollama-helm \
  --set replicaCount=3 \
  --set persistence.accessMode=ReadWriteMany  # RWX debe soportarse
```

**Nota:** Requiere storage que soporte RWX (NFS, etc.)

## Backup and Restore

### Backup de modelos

```bash
# Crear backup del almacenamiento
kubectl exec -it deployment/ollama -- tar czf - /root/.ollama | \
  tar xzf - -C /tmp/ollama-backup

# O usando Kubernetes backup
kubectl exec deployment/ollama -- tar czf - /root/.ollama > ollama-models-backup.tar.gz
```

### Restore de modelos

```bash
kubectl cp ollama-models-backup.tar.gz <namespace>/<pod-name>:/tmp/backup.tar.gz
kubectl exec <pod-name> -- tar xzf /tmp/backup.tar.gz -C /
```

## Integration with LiteLLM

Para integrar Ollama con LiteLLM en el core:

```python
from litellm import completion

response = completion(
    model="ollama/mistral",
    api_base="http://ollama.shokanllm.svc.cluster.local:11434",
    messages=[
        {"role": "user", "content": "Hello!"}
    ]
)
```

## Troubleshooting Commands

```bash
# Ver ambiente del pod
kubectl exec deployment/ollama -- env | grep OLLAMA

# Ver limites de recursos
kubectl exec deployment/ollama -- ulimit -a

# Ver uso de memoria en el pod
kubectl exec deployment/ollama -- free -h

# Ver procesos
kubectl exec deployment/ollama -- ps aux

# Verificar conectividad
kubectl exec deployment/ollama -- ping -c 1 ollama.ai

# Obtener info del sistema
kubectl exec deployment/ollama -- uname -a
```

## References

- [Ollama GitHub - Configuration](https://github.com/ollama/ollama#model-configuration)
- [Ollama API Docs](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [NVIDIA CUDA Compute Capability](https://developer.nvidia.com/cuda-gpus)
