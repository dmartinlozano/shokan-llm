"""Kubernetes secret read/write helpers."""

import base64
import datetime
import json
import os

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config


class K8s:
    """Read and write keys in the shokanllm-secret Kubernetes Secret.

    Uses in-cluster config when running in a pod; falls back to kubeconfig
    for local development. Requires RBAC: get + patch on secrets/shokanllm-secret.
    """

    def __init__(self) -> None:
        self.namespace = os.getenv("KUBERNETES_NAMESPACE", "shokanllm")
        self.secret_name = "shokanllm-secret"
        self._v1 = None

    def _api(self):
        if self._v1 is None:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            self._v1 = k8s_client.CoreV1Api()
        return self._v1

    def read(self, key: str) -> str:
        """Return the decoded value of a secret key, or '' if not found."""
        secret = self._api().read_namespaced_secret(self.secret_name, self.namespace)
        raw = (secret.data or {}).get(key, "")
        return base64.b64decode(raw).decode() if raw else ""

    def write(self, key: str, value: str) -> None:
        """Write a string value to a secret key (base64-encoded)."""
        patch = {"data": {key: base64.b64encode(value.encode()).decode()}}
        self._api().patch_namespaced_secret(self.secret_name, self.namespace, patch)

    def delete_key(self, key: str) -> None:
        """Remove a key from the secret by patching its value to null."""
        try:
            patch = {"data": {key: None}}
            self._api().patch_namespaced_secret(self.secret_name, self.namespace, patch)
        except Exception:
            pass

    def read_all_keys(self) -> dict[str, str]:
        """Return all key-value pairs from shokanllm-secret as a flat string dict."""
        secret = self._api().read_namespaced_secret(self.secret_name, self.namespace)
        result = {}
        for key, encoded in (secret.data or {}).items():
            try:
                result[key] = base64.b64decode(encoded).decode()
            except Exception:
                result[key] = ""
        return result

    def read_json(self, key: str) -> dict:
        """Return a JSON-decoded dict from a secret key, or {} if absent/invalid."""
        raw = self.read(key)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def write_json(self, key: str, value: dict) -> None:
        """Serialize dict to JSON and write to a secret key."""
        self.write(key, json.dumps(value))

    # ── PersistentVolumeClaim management ───────────────────────────────────────

    def list_pvcs(self, label_selector: str = "shokan-rag=true") -> list[dict]:
        """Return PVCs matching label_selector: [{name, status, capacity, storage_class}]."""
        pvcs = self._api().list_namespaced_persistent_volume_claim(
            self.namespace, label_selector=label_selector
        ).items
        return [
            {
                "name": p.metadata.name,
                "status": p.status.phase,
                "capacity": (p.status.capacity or {}).get("storage", "—"),
                "storage_class": p.spec.storage_class_name or "—",
                "access_modes": p.spec.access_modes or [],
            }
            for p in pvcs
        ]

    def create_pvc(
        self,
        name: str,
        size_gi: int,
        storage_class: str = "standard",
        access_mode: str = "ReadWriteOnce",
    ) -> None:
        """Create a PVC labelled shokan-rag=true."""
        pvc = k8s_client.V1PersistentVolumeClaim(
            metadata=k8s_client.V1ObjectMeta(
                name=name,
                namespace=self.namespace,
                labels={"shokan-rag": "true"},
            ),
            spec=k8s_client.V1PersistentVolumeClaimSpec(
                access_modes=[access_mode],
                resources=k8s_client.V1VolumeResourceRequirements(
                    requests={"storage": f"{size_gi}Gi"}
                ),
                storage_class_name=storage_class,
            ),
        )
        self._api().create_namespaced_persistent_volume_claim(self.namespace, pvc)

    def delete_pvc(self, name: str) -> None:
        """Delete a PVC by name."""
        self._api().delete_namespaced_persistent_volume_claim(name, self.namespace)

    def list_storage_classes(self) -> list[str]:
        """Return available StorageClass names in the cluster."""
        try:
            api = k8s_client.StorageV1Api()
            return [sc.metadata.name for sc in api.list_storage_class().items]
        except Exception:
            return ["standard"]

    # ── Deployment image management ────────────────────────────────────────────

    def get_deployment_image(self, deployment_name: str) -> str:
        """Return the current image of the first container in a deployment."""
        try:
            apps = k8s_client.AppsV1Api()
            deploy = apps.read_namespaced_deployment(deployment_name, self.namespace)
            return deploy.spec.template.spec.containers[0].image
        except Exception:
            return ""

    # ── System models ─────────────────────────────────────────────────────────

    def list_system_models(self) -> list[str]:
        """Return the list of Ollama models protected from stop/delete."""
        raw = self.read("system-models")
        if raw:
            try:
                return json.loads(raw)
            except Exception:
                pass
        default = ["nomic-embed-text"]
        self.write("system-models", json.dumps(default))
        return default

    def set_system_models(self, models: list[str]) -> None:
        """Persist the system models list."""
        self.write("system-models", json.dumps(models))

    # ── CronJob management ────────────────────────────────────────────────────

    def list_cronjobs(self, label_selector: str = "") -> list[dict]:
        """Return CronJobs in the namespace as plain dicts."""
        batch = k8s_client.BatchV1Api()
        items = batch.list_namespaced_cron_job(
            self.namespace, label_selector=label_selector
        ).items
        now = datetime.datetime.now(datetime.timezone.utc)
        result = []
        for cj in items:
            last_run = cj.status.last_schedule_time if cj.status else None
            last_ok  = cj.status.last_successful_time if cj.status else None
            age_secs = int((now - last_run).total_seconds()) if last_run else 0
            ok_secs  = int((now - last_ok).total_seconds())  if last_ok  else None
            result.append({
                "k8s_name":       cj.metadata.name,
                "schedule":       cj.spec.schedule,
                "suspended":      bool(cj.spec.suspend),
                "age_secs":       age_secs,
                "last_ok_secs":   ok_secs,
            })
        return result

    def patch_cronjob_schedule(self, name: str, schedule: str) -> None:
        """Update the schedule expression of a CronJob."""
        k8s_client.BatchV1Api().patch_namespaced_cron_job(
            name, self.namespace, {"spec": {"schedule": schedule}}
        )

    def suspend_cronjob(self, name: str, suspend: bool) -> None:
        """Suspend (stop) or unsuspend (start) a CronJob."""
        k8s_client.BatchV1Api().patch_namespaced_cron_job(
            name, self.namespace, {"spec": {"suspend": suspend}}
        )

    def trigger_cronjob(self, name: str) -> str:
        """Create a one-off Job from a CronJob template immediately. Returns job name."""
        batch = k8s_client.BatchV1Api()
        cj = batch.read_namespaced_cron_job(name, self.namespace)
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        job_name = f"{name}-manual-{ts}"
        job = k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                annotations={"cronjob.kubernetes.io/instantiate": "manual"},
                owner_references=[k8s_client.V1OwnerReference(
                    api_version="batch/v1",
                    kind="CronJob",
                    name=name,
                    uid=cj.metadata.uid,
                    block_owner_deletion=True,
                    controller=True,
                )],
            ),
            spec=cj.spec.job_template.spec,
        )
        batch.create_namespaced_job(self.namespace, job)
        return job_name

    def get_cronjob_env(self, name: str, env_var: str) -> str:
        """Return the plain-value of an env var from the first container of a CronJob."""
        cj = k8s_client.BatchV1Api().read_namespaced_cron_job(name, self.namespace)
        containers = cj.spec.job_template.spec.template.spec.containers
        if not containers:
            return ""
        for env in containers[0].env or []:
            if env.name == env_var and env.value is not None:
                return env.value
        return ""

    def patch_cronjob_env(self, name: str, env_var: str, value: str) -> None:
        """Update a single plain-value env var on the first container of a CronJob.

        Preserves all other env entries (including secretKeyRef / configMapKeyRef).
        """
        batch = k8s_client.BatchV1Api()
        cj = batch.read_namespaced_cron_job(name, self.namespace)
        containers = cj.spec.job_template.spec.template.spec.containers
        if not containers:
            return

        updated_env: list[dict] = []
        found = False
        for env in containers[0].env or []:
            if env.name == env_var:
                updated_env.append({"name": env_var, "value": str(value)})
                found = True
            elif env.value is not None:
                updated_env.append({"name": env.name, "value": env.value})
            elif env.value_from:
                vf = env.value_from
                if vf.secret_key_ref:
                    updated_env.append({"name": env.name, "valueFrom": {"secretKeyRef": {
                        "name": vf.secret_key_ref.name,
                        "key":  vf.secret_key_ref.key,
                    }}})
                elif vf.config_map_key_ref:
                    updated_env.append({"name": env.name, "valueFrom": {"configMapKeyRef": {
                        "name": vf.config_map_key_ref.name,
                        "key":  vf.config_map_key_ref.key,
                    }}})

        if not found:
            updated_env.append({"name": env_var, "value": str(value)})

        patch = {
            "spec": {
                "jobTemplate": {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {"name": containers[0].name, "env": updated_env}
                                ]
                            }
                        }
                    }
                }
            }
        }
        batch.patch_namespaced_cron_job(name, self.namespace, patch)

    def upgrade_deployment_image(self, deployment_name: str, image: str) -> None:
        """Set a new image on a deployment's first container and trigger a rollout."""
        apps = k8s_client.AppsV1Api()
        deploy = apps.read_namespaced_deployment(deployment_name, self.namespace)
        container_name = deploy.spec.template.spec.containers[0].name
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()
                        }
                    },
                    "spec": {
                        "containers": [
                            {"name": container_name, "image": image, "imagePullPolicy": "Always"}
                        ]
                    },
                }
            }
        }
        apps.patch_namespaced_deployment(deployment_name, self.namespace, patch)
