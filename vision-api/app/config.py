"""Cấu hình qua biến môi trường. Xem .env.example."""
import json
import os

EMB_DIM = 512

INSIGHTFACE_MODEL = os.getenv("INSIGHTFACE_MODEL", "buffalo_l")
MODEL_ROOT = os.getenv("MODEL_ROOT", "/data/models/insightface")
DET_SIZE = int(os.getenv("DET_SIZE", "640"))
DET_THRESH = float(os.getenv("DET_THRESH", "0.5"))

MATCH_THRESHOLD = float(os.getenv("MATCH_THRESHOLD", "0.40"))
TOP_K = int(os.getenv("TOP_K", "5"))

BACKEND_URL = os.getenv("VISION_BACKEND_URL", "http://mock-backend:9000")
BACKEND_INTERNAL_KEY = os.getenv("VISION_BACKEND_INTERNAL_KEY", "dev-internal-key")
BACKEND_TIMEOUT = float(os.getenv("VISION_BACKEND_TIMEOUT", "20"))
POST_EVENTS = os.getenv("VISION_POST_EVENTS", "true").lower() == "true"
PREFETCH_ON_START = os.getenv("VISION_PREFETCH_ON_START", "true").lower() == "true"

GPU_CONCURRENCY = int(os.getenv("VISION_GPU_CONCURRENCY", "1"))
RATE_LIMIT_PER_MIN = int(os.getenv("VISION_RATE_LIMIT_PER_MIN", "120"))
MAX_IMAGE_BYTES = int(os.getenv("VISION_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("VISION_MAX_IMAGE_PIXELS", str(40_000_000)))

# SSRF: tải ảnh theo URL. Mặc định bật nhưng chặn IP nội bộ + không theo redirect.
ALLOW_URL_FETCH = os.getenv("VISION_ALLOW_URL_FETCH", "true").lower() == "true"
URL_ALLOWLIST = [d.strip().lower() for d in os.getenv("VISION_URL_ALLOWLIST", "").split(",") if d.strip()]

METRICS_ENABLED = os.getenv("VISION_METRICS_ENABLED", "true").lower() == "true"

# VISION_API_TOKENS = JSON: {"<token>": {"tenant": "t_demo" | "*", "role": "client" | "admin"}}
_raw = os.getenv("VISION_API_TOKENS", "").strip()
if _raw:
    API_TOKENS = json.loads(_raw)
else:  # mặc định chỉ dùng cho dev/local
    API_TOKENS = {
        "tok_demo_client": {"tenant": "t_demo", "role": "client"},
        "tok_admin_root": {"tenant": "*", "role": "admin"},
    }
