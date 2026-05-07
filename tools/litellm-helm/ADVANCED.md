# Advanced Configuration for LiteLLM

## Advanced Model Configuration

### Configurar fallback entre modelos

```yaml
configMap:
  enabled: true
  litellm_config_yaml: |
    model_list:
      - model_name: "llm-primary"
        litellm_params:
          model: "gpt-4"
      - model_name: "llm-fallback"
        litellm_params:
          model: "claude-3-opus-20240229"
      - model_name: "llm-local"
        litellm_params:
          model: "ollama/mistral"
          api_base: "http://ollama:11434"
    
    router_settings:
      model_fallback_map:
        gpt-4: "claude-3-opus-20240229"
        claude-3-opus-20240229: "ollama-mistral"
```

### Configurar limits y retry policies

```yaml
router_settings:
  default_max_retries: 5
  default_timeout: 120
  fallback_on_bad_llm_2_request_timeout: true
  deployment_id: "production-proxy"
```

## API Key Management

### Crear secretos para múltiples proveedores

```bash
kubectl create secret generic litellm-keys \
  --from-literal=openai-key=$OPENAI_API_KEY \
  --from-literal=anthropic-key=$ANTHROPIC_API_KEY \
  --from-literal=cohere-key=$COHERE_API_KEY \
  --from-literal=huggingface-key=$HUGGINGFACE_API_KEY \
  -n shokanllm
```

### Referencias en el deployment

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

## Database Integration

### Configurar PostgreSQL para logging y caching

```yaml
litellm:
  database:
    enabled: true
    url: "postgresql://litellm:secure_password@postgres.shokanllm.svc.cluster.local:5432/litellm"

configMap:
  enabled: true
  litellm_config_yaml: |
    general_settings:
      database_type: "postgres"
      database_connection_pool_size: 20
      proxy_budget_ratelimit_key: "user-id"
      log_level: "debug"
```

### Crear tablas necesarias

```bash
kubectl exec -it deployment/litellm -- \
  litellm --migration_name create_tables
```

## Rate Limiting and Access Control

### Configurar límites por usuario

```yaml
configMap:
  enabled: true
  litellm_config_yaml: |
    general_settings:
      proxy_budget_ratelimit_key: "user-id"
    
    model_list:
      - model_name: "gpt-4"
        litellm_params:
          model: "gpt-4"
        max_parallel_requests: 10
        max_tokens_per_day: 100000
```

### Usar API keys con límites

```bash
# Crear una API key con límites
curl -X POST http://localhost:8000/key/generate \
  -H "Content-Type: application/json" \
  -d '{
    "max_budget": 100.00,
    "user_id": "user-123",
    "models": ["gpt-4", "claude-3-opus"]
  }'
```

## Load Balancing and Routing

### Round-robin entre múltiples instancias

```yaml
configMap:
  enabled: true
  litellm_config_yaml: |
    model_list:
      - model_name: "ollama-local-1"
        litellm_params:
          model: "ollama/mistral"
          api_base: "http://ollama-1:11434"
      - model_name: "ollama-local-2"
        litellm_params:
          model: "ollama/mistral"
          api_base: "http://ollama-2:11434"
    
    router_settings:
      model_list_routing_mode: "random"
```

### Weighted routing

```yaml
model_list:
  - model_name: "fast-llm"
    litellm_params:
      model: "gpt-4"
    weight: 0.7
  - model_name: "cheap-llm"
    litellm_params:
      model: "claude-3-sonnet"
    weight: 0.3
```

## Monitoring and Observability

### Prometheus Metrics

```yaml
service:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "8000"
    prometheus.io/path: "/metrics"
```

### Custom logging

```yaml
litellm:
  logging:
    level: "DEBUG"
    sql_logs: true
    logFile: "/app/logs/litellm.log"

env:
  - name: LITELLM_LOG_LEVEL
    value: "DEBUG"
  - name: LITELLM_LOG_FILE
    value: "/app/logs/litellm.log"
```

## Custom Headers and Middleware

### Agregar headers personalizados

```yaml
env:
  - name: LITELLM_HEADERS
    value: '{"X-Custom-Header": "custom-value"}'
```

## Scaling and Performance

### Horizontal scaling con shared database

```bash
helm install litellm ./litellm-helm \
  --set replicaCount=3 \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=3 \
  --set autoscaling.maxReplicas=10 \
  --set litellm.database.enabled=true
```

### Resource optimization

```yaml
resources:
  limits:
    cpu: 8000m
    memory: 8Gi
  requests:
    cpu: 4000m
    memory: 4Gi
```

## Multi-Region Deployment

### Configurar LiteLLM en múltiples regiones

```bash
# Region 1
helm install litellm-us ./litellm-helm \
  --set fullnameOverride=litellm-us \
  --set nodeSelector.region=us-east \
  -n shokanllm

# Region 2
helm install litellm-eu ./litellm-helm \
  --set fullnameOverride=litellm-eu \
  --set nodeSelector.region=eu-west \
  -n shokanllm
```

## Custom Model Providers

### Agregar proveedores personalizados

```yaml
configMap:
  litellm_config_yaml: |
    model_list:
      - model_name: "custom-provider"
        litellm_params:
          model: "custom_provider/model-name"
          api_base: "https://custom-api.example.com"
          api_key: "$CUSTOM_API_KEY"
```

## Proxy Caching

### Habilitar caching de respuestas

```yaml
configMap:
  enabled: true
  litellm_config_yaml: |
    general_settings:
      database_type: "postgres"
      enable_cache: true
      cache_ttl: 3600
```

## Debugging and Troubleshooting

### Activar verbose logging

```yaml
env:
  - name: LITELLM_LOG_LEVEL
    value: "DEBUG"
  - name: LITELLM_VERBOSE
    value: "true"
```

### Inspeccionar configuración en tiempo real

```bash
# Ver la configuración actual
kubectl exec -it deployment/litellm -- curl http://localhost:8000/config

# Ver status del proxy
kubectl exec -it deployment/litellm -- curl http://localhost:8000/status

# Ver estadísticas
kubectl exec -it deployment/litellm -- curl http://localhost:8000/stats
```

## Integration with Monitoring Stack

### Prometheus ServiceMonitor

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: litellm
  namespace: shokanllm
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: litellm
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
```

### Grafana Dashboard

```bash
# Importar dashboard Grafana
kubectl create configmap grafana-litellm-dashboard \
  --from-file=dashboard.json \
  -n monitoring
```

## Security Hardening

### Network Policies

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: litellm-network-policy
  namespace: shokanllm
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: litellm
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              role: client
      ports:
        - protocol: TCP
          port: 8000
  egress:
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: ollama
      ports:
        - protocol: TCP
          port: 11434
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: TCP
          port: 53  # DNS
```

### Pod Security Policy

```yaml
apiVersion: policy/v1beta1
kind: PodSecurityPolicy
metadata:
  name: litellm-psp
spec:
  privileged: false
  allowPrivilegeEscalation: false
  requiredDropCapabilities:
    - ALL
  volumes:
    - 'configMap'
    - 'emptyDir'
    - 'persistentVolumeClaim'
  hostNetwork: false
  hostIPC: false
  hostPID: false
  runAsUser:
    rule: 'MustRunAsNonRoot'
  seLinux:
    rule: 'MustRunAs'
```

## Disaster Recovery

### Backup de configuración

```bash
# Exportar configuración
kubectl get cm litellm-config -o yaml > litellm-config-backup.yaml

# Exportar datos
kubectl exec deployment/litellm -- \
  pg_dump postgresql://user:pass@postgres:5432/litellm > backup.sql
```

## Performance Tips

1. **Connection Pooling:** Ajusta el pool size según demanda
2. **Caching:** Habilita caching de respuestas comunes
3. **Rate Limiting:** Configura límites apropiados
4. **Monitoring:** Monitorea latencia y errores
5. **Fallback:** Configura fallback para alta disponibilidad

## References

- [LiteLLM Advanced Config](https://docs.litellm.ai/docs/proxy/configs)
- [LiteLLM Router](https://docs.litellm.ai/docs/proxy/router)
- [LiteLLM Authentication](https://docs.litellm.ai/docs/proxy/key_management)
