"""Prometheus metrics. Cardinality: label `tenant` — ổn khi số tenant nhỏ."""
import subprocess

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS = Counter(
    "vision_requests_total", "HTTP requests", ["endpoint", "method", "status"]
)
REQ_DURATION = Histogram(
    "vision_request_duration_seconds", "HTTP request duration", ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
INFERENCE = Histogram(
    "vision_inference_duration_seconds", "GPU inference duration", ["kind"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)
OBJECTS_DETECTED = Counter(
    "vision_objects_detected_total", "Faces/products detected", ["tenant", "modality"]
)
MATCHES = Counter(
    "vision_matches_total", "Search hits (match != null)", ["tenant", "modality"]
)
RATELIMIT_REJECTS = Counter(
    "vision_ratelimit_rejections_total", "Requests rejected by rate limiter", ["tenant"]
)
INDEX_VECTORS = Gauge("vision_index_vectors", "FAISS vectors per tenant", ["tenant", "modality"])
INDEX_ITEMS = Gauge("vision_index_items", "Indexed persons/products per tenant", ["tenant", "modality"])
READY = Gauge("vision_ready", "1 khi model+FAISS sẵn sàng")
GPU_VRAM_USED = Gauge("vision_gpu_vram_used_mb", "VRAM used (MB)")
GPU_VRAM_TOTAL = Gauge("vision_gpu_vram_total_mb", "VRAM total (MB)")

_NORMAL_PATHS = {
    "/", "/health", "/ready", "/gpu", "/metrics", "/docs", "/redoc", "/openapi.json",
    "/v1/faces/detect", "/v1/faces/embed", "/v1/faces/search", "/v1/index/stats",
    "/v1/products/embed", "/v1/products/search", "/v1/products/index/stats",
    "/admin/reload",
}


def norm_path(path: str) -> str:
    return path if path in _NORMAL_PATHS else "other"


def refresh_index_gauges(stats: dict, modality: str) -> None:
    for tenant, s in stats.items():
        INDEX_VECTORS.labels(tenant=tenant, modality=modality).set(s.get("vectors", 0))
        INDEX_ITEMS.labels(tenant=tenant, modality=modality).set(s.get("items", s.get("persons", 0)))


def refresh_gpu_gauges() -> None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        total, used = (int(x.strip()) for x in out.split(","))
        GPU_VRAM_TOTAL.set(total)
        GPU_VRAM_USED.set(used)
    except Exception:  # noqa: BLE001
        pass


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
