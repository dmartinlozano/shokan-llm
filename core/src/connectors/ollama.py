"""Ollama runtime client and Hugging Face Hub model browser."""

import httpx

from config import OLLAMA_URL as _OLLAMA_URL
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

_HF_API = "https://huggingface.co/api"

_TASK_LABELS: dict[str, str] = {
    "text-generation": "Chat / text generation",
    "text2text-generation": "Text transformation",
    "text-classification": "Classification",
    "token-classification": "Named entity recognition",
    "question-answering": "Question answering",
    "summarization": "Summarization",
    "translation": "Translation",
    "fill-mask": "Fill mask",
    "feature-extraction": "Embeddings",
    "code-generation": "Code generation",
}

# Common quantization suffixes ordered best→smallest quality
_QUANT_ORDER = ["Q8_0", "Q6_K", "Q5_K_M", "Q4_K_M", "Q4_K_S", "Q3_K_M", "Q2_K"]


class Ollama:
    """Client for the Ollama runtime API and the Hugging Face Hub model catalog.

    Local API reads OLLAMA_URL from env.
    Tenant fit check uses the Kubernetes API to read allocatable node RAM.
    """

    def __init__(self) -> None:
        self.url = _OLLAMA_URL

    # ── Local Ollama API ───────────────────────────────────────────────────────

    async def list_local(self) -> list[dict]:
        """Return installed models: [{name, size, size_gb, modified_at, details}]."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/api/tags", timeout=5.0)
                if r.is_success:
                    models = r.json().get("models", [])
                    for m in models:
                        m["size_gb"] = round(m.get("size", 0) / 1024**3, 2)
                    return models
            except Exception:
                pass
        return []

    async def model_info(self, name: str) -> dict:
        """Return full details of an installed model (architecture, parameters, quantization…)."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.post(f"{self.url}/api/show", json={"name": name}, timeout=5.0)
                if r.is_success:
                    return r.json()
            except Exception:
                pass
        return {}

    async def running_models(self) -> list[dict]:
        """Return models currently loaded in RAM: [{name, size_vram, until}]."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/api/ps", timeout=5.0)
                if r.is_success:
                    return r.json().get("models", [])
            except Exception:
                pass
        return []

    async def pull(self, name: str) -> None:
        """Pull a model from Ollama registry or Hugging Face (hf.co/<repo>:<file>).

        Blocks until the download is complete. Call from a background task.
        """
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"{self.url}/api/pull",
                json={"name": name, "stream": False},
                timeout=3600.0,
            )
            r.raise_for_status()

    async def load(self, name: str, keep_alive: int | str = -1) -> None:
        """Load a model into RAM without running inference. keep_alive=-1 means indefinite."""
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"{self.url}/api/generate",
                json={"model": name, "keep_alive": keep_alive, "stream": False},
                timeout=120.0,
            )
            r.raise_for_status()

    async def unload(self, name: str) -> None:
        """Force-unload a model from RAM immediately."""
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"{self.url}/api/generate",
                json={"model": name, "keep_alive": 0, "stream": False},
                timeout=15.0,
            )
            r.raise_for_status()

    async def delete(self, name: str) -> None:
        """Delete a locally installed model."""
        async with httpx.AsyncClient() as http:
            r = await http.request(
                "DELETE",
                f"{self.url}/api/delete",
                json={"name": name},
                timeout=10.0,
            )
            r.raise_for_status()

    async def is_reachable(self) -> bool:
        """Return True if the Ollama server responds."""
        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{self.url}/", timeout=3.0)
                return r.is_success
            except Exception:
                return False

    # ── Hugging Face Hub browser ───────────────────────────────────────────────

    async def search_hub(
        self,
        query: str = "",
        task: str = "",
        limit: int = 40,
    ) -> list[dict]:
        """Search GGUF models on Hugging Face Hub.

        Returns list of:
          {id, name, task, task_label, size_gb, variants, downloads, likes, pull_id}

        pull_id is the string to pass to Ollama pull: "hf.co/<id>"
        variants: [{filename, quant, size_gb}] sorted by quality desc.
        """
        params: dict = {
            "library": "gguf",
            "sort": "downloads",
            "limit": limit,
            "full": "true",
        }
        if query:
            params["search"] = query
        if task:
            params["pipeline_tag"] = task

        async with httpx.AsyncClient() as http:
            try:
                r = await http.get(f"{_HF_API}/models", params=params, timeout=15.0)
                if not r.is_success:
                    return []
                data = r.json()
            except Exception:
                return []

        results = []
        for m in data:
            siblings = m.get("siblings") or []
            variants = _parse_gguf_variants(siblings)
            if not variants:
                continue

            min_size = min((v["size_gb"] for v in variants if v["size_gb"]), default=None)
            results.append({
                "id": m["id"],
                "name": m["id"].split("/")[-1],
                "task": m.get("pipeline_tag", ""),
                "task_label": _TASK_LABELS.get(m.get("pipeline_tag", ""), m.get("pipeline_tag") or "—"),
                "size_gb": min_size,
                "variants": variants,
                "downloads": m.get("downloads", 0),
                "likes": m.get("likes", 0),
                "pull_id": f"hf.co/{m['id']}",
            })
        return results

    # ── Tenant resource check ──────────────────────────────────────────────────

    def cluster_allocatable_ram_gb(self) -> float:
        """Return total allocatable RAM (GB) across all schedulable nodes."""
        try:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

            v1 = k8s_client.CoreV1Api()
            total = 0.0
            for node in v1.list_node().items:
                taints = node.spec.taints or []
                if any(t.effect == "NoSchedule" for t in taints):
                    continue
                mem = (node.status.allocatable or {}).get("memory", "0Ki")
                total += _parse_k8s_memory_gb(mem)
            return round(total, 1)
        except Exception:
            return 0.0

    def fits_in_tenant(self, size_gb: float | None, available_ram_gb: float | None = None) -> tuple[bool, str]:
        """Return (fits, reason) for a model of size_gb against cluster RAM.

        available_ram_gb: pre-computed value to avoid a blocking K8s call; fetched on-demand if None.
        Rule: model needs size_gb × 1.15 to load comfortably (GGUF overhead).
        """
        if not size_gb:
            return True, "Unknown size — cannot verify"

        required = round(size_gb * 1.15, 1)
        available = available_ram_gb if available_ram_gb is not None else 0.0

        if available <= 0:
            return True, "Cluster metrics unavailable"

        if required <= available:
            return True, f"Needs {required} GB / {available} GB available"
        return False, f"Needs {required} GB but only {available} GB available"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_gguf_variants(siblings: list[dict]) -> list[dict]:
    """Extract GGUF file variants with quantization label and size."""
    variants = []
    for s in siblings:
        fname = s.get("rfilename", "")
        if not fname.lower().endswith(".gguf"):
            continue
        size_bytes = s.get("size", 0)
        quant = _extract_quant(fname)
        variants.append({
            "filename": fname,
            "quant": quant,
            "size_gb": round(size_bytes / 1024**3, 1) if size_bytes else None,
        })

    def sort_key(v):
        try:
            return _QUANT_ORDER.index(v["quant"])
        except ValueError:
            return len(_QUANT_ORDER)

    return sorted(variants, key=sort_key)


def _extract_quant(filename: str) -> str:
    """Extract quantization label from a GGUF filename."""
    upper = filename.upper()
    for q in _QUANT_ORDER:
        if q in upper:
            return q
    return "—"


def _parse_k8s_memory_gb(mem_str: str) -> float:
    """Convert a K8s memory string (Ki, Mi, Gi, k, M, G) to GB."""
    suffixes = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "k": 1_000, "M": 1_000_000, "G": 1_000_000_000}
    for suffix, factor in suffixes.items():
        if mem_str.endswith(suffix):
            return int(mem_str[: -len(suffix)]) * factor / 1024**3
    try:
        return int(mem_str) / 1024**3
    except ValueError:
        return 0.0
