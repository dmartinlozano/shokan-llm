"""Global configuration — environment variables and constants."""
import os

OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "shokan-core")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
KC_URL = os.getenv("KEYCLOAK_URL", "http://keycloak.shokanllm.svc.cluster.local:8080")
KC_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL") or KC_URL
KC_REALM = os.getenv("KEYCLOAK_REALM", "shokanllm")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
SHOKAN_URL = os.getenv("SHOKAN_URL", "http://localhost:7860")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "ollama/llama3")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama.shokanllm.svc.cluster.local:11434")
