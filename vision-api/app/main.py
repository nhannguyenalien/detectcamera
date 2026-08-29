"""
vision-api — face detection + recognition (multi-tenant) trên GPU.

Boot sequence (gate /ready):
  load model (SCRFD+ArcFace)  ->  warmup GPU  ->  sync embeddings từ backend
  ->  build FAISS index / tenant  ->  ready=true

Auth mọi endpoint /v1/* và /admin/*:  Authorization: Bearer <token>  + (X-Tenant-ID)
"""
import asyncio
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from . import config, metrics, net, schemas
from .backend import BackendClient
from .deps import Auth, RateLimiter, auth_ctx, require_tenant
from .engine import FaceEngine
from .index import IndexStore

STATE = {"ready": False, "detail": "starting", "started_at": time.time()}

engine = FaceEngine()
backend = BackendClient()
store = IndexStore(backend)
gpu_sem = asyncio.Semaphore(config.GPU_CONCURRENCY)
limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)

DESCRIPTION = """\
API nhận dạng **khuôn mặt** chạy trên GPU (InsightFace SCRFD + ArcFace) với **FAISS** index
trong RAM, **multi-tenant**.

### Xác thực
Mọi endpoint `/v1/*` và `/admin/*` cần:

| Header | Bắt buộc | Ghi chú |
|---|---|---|
| `Authorization: Bearer <token>` | ✅ | token cấp cho 1 tenant, hoặc token global `*` |
| `X-Tenant-ID: <tenant>` | khi token là global `*` | chọn tenant thao tác |
| `X-Request-ID: <id>` | tùy chọn | echo lại trong response; tự sinh nếu thiếu |

Token `role=admin` mới gọi được `/admin/reload`.
Rate limit theo tenant (HTTP 429 khi vượt).

### Luồng enroll 1 người
1. `POST /v1/faces/embed` với ảnh chân dung → lấy `faces[i].embedding` (512-d).
2. Gửi embedding sang backend (source of truth) — `POST {backend}/internal/tenants/{tid}/persons`.
3. `POST /admin/reload?tenant_id={tid}` để nạp lại FAISS.
4. `POST /v1/faces/search` → trả `person_id` + `score`.

### Ảnh đầu vào
`multipart/form-data`: field `file` (upload) **hoặc** field `url` (link ảnh http/https).
URL bị chặn nếu trỏ vào IP nội bộ/loopback, và không theo redirect (chống SSRF).
Giới hạn dung lượng `VISION_MAX_IMAGE_BYTES` (mặc định 20MB), số pixel `VISION_MAX_IMAGE_PIXELS`.

### Vận hành
`GET /metrics` — Prometheus. `GET /health` liveness, `GET /ready` readiness.
"""

TAGS = [
    {"name": "infra", "description": "Liveness / readiness / GPU / metrics. Không cần auth."},
    {"name": "faces", "description": "Detect / embed / search khuôn mặt. Cần token client."},
    {"name": "admin", "description": "Quản trị index. Cần token role=admin."},
    {"name": "meta", "description": "Bản mô tả API cho client / AI agent tự khám phá."},
]

COMMON_ERRORS = {
    400: {"model": schemas.ErrorResponse, "description": "Ảnh/URL không hợp lệ"},
    401: {"model": schemas.ErrorResponse, "description": "Thiếu / sai token"},
    403: {"model": schemas.ErrorResponse, "description": "Sai tenant hoặc thiếu quyền admin"},
    429: {"model": schemas.ErrorResponse, "description": "Vượt rate limit của tenant"},
    503: {"model": schemas.ErrorResponse, "description": "Service chưa `ready`"},
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    loop = asyncio.get_event_loop()
    try:
        STATE["detail"] = "loading model"
        await loop.run_in_executor(None, engine.load)

        STATE["detail"] = "warmup gpu"
        await loop.run_in_executor(None, engine.warmup)

        if config.PREFETCH_ON_START:
            STATE["detail"] = "sync embeddings"
            try:
                tids = await store.reload_all()
                STATE["detail"] = f"indexed tenants={tids}"
            except Exception as e:  # noqa: BLE001 - vẫn ready, tenant sẽ lazy-load sau
                STATE["detail"] = f"prefetch failed, lazy later: {e}"

        STATE["ready"] = True
        STATE["detail"] = STATE["detail"] if "fail" in STATE["detail"] else "ready"
        metrics.READY.set(1)
        metrics.refresh_index_gauges(store.stats())
        print(f"[boot] READY provider={engine.provider} {STATE['detail']}")
    except Exception as e:  # noqa: BLE001
        STATE["ready"] = False
        STATE["detail"] = f"startup error: {e}"
        metrics.READY.set(0)
        print(f"[boot] FAILED: {e}")
        raise
    yield
    await backend.close()


app = FastAPI(
    title="vision-api (face)",
    version="1.1.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    contact={"name": "vision-stack", "url": "http://192.168.1.50:18090/docs"},
    license_info={"name": "internal"},
    lifespan=lifespan,
)


@app.middleware("http")
async def _metrics_mw(request: Request, call_next):
    if not config.METRICS_ENABLED:
        return await call_next(request)
    ep = metrics.norm_path(request.url.path)
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
        status = resp.status_code
    except Exception:
        metrics.REQUESTS.labels(ep, request.method, "500").inc()
        raise
    metrics.REQ_DURATION.labels(ep).observe(time.perf_counter() - t0)
    metrics.REQUESTS.labels(ep, request.method, str(status)).inc()
    return resp


# ------------------------------- helpers ------------------------------------- #

async def _read_image(file: Optional[UploadFile], url: Optional[str]) -> bytes:
    if file is not None:
        raw = await file.read(config.MAX_IMAGE_BYTES + 1)
        if len(raw) > config.MAX_IMAGE_BYTES:
            raise HTTPException(413, "Ảnh quá lớn")
        return raw
    if url:
        try:
            net.assert_fetchable(url)
            return await net.fetch_image(url, config.MAX_IMAGE_BYTES)
        except net.UrlNotAllowed as e:
            raise HTTPException(400, f"URL không dùng được: {e}")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Không tải được ảnh: {e}")
    raise HTTPException(422, "Cần 'file' (multipart) hoặc 'url'")


async def _analyze(raw: bytes, kind: str):
    loop = asyncio.get_event_loop()
    try:
        bgr = await loop.run_in_executor(None, engine.decode, raw)
    except Exception as e:  # noqa: BLE001 - ảnh hỏng / bomb
        raise HTTPException(400, f"Ảnh không hợp lệ: {e}")
    async with gpu_sem:
        faces, ms = await loop.run_in_executor(None, engine.analyze, bgr)
    if config.METRICS_ENABLED:
        metrics.INFERENCE.labels(kind).observe(ms / 1000.0)
    return faces, ms


def _guard_ready() -> None:
    if not STATE["ready"]:
        raise HTTPException(503, f"Service chưa sẵn sàng: {STATE['detail']}")


async def _rate_check(auth: Auth) -> None:
    try:
        await limiter.check(auth.tenant)
    except HTTPException:
        if config.METRICS_ENABLED:
            metrics.RATELIMIT_REJECTS.labels(auth.tenant).inc()
        raise


# ------------------------------- meta -------------------------------------- #

@app.get("/", tags=["meta"], response_model=schemas.RootManifest, summary="Manifest cho client/agent")
async def root():
    """Bản mô tả gọn: endpoint, cách auth, luồng enroll. Đọc cái này trước khi gọi API."""
    return {
        "service": "vision-api (face)",
        "version": app.version,
        "ready_url": "/ready",
        "docs": {"swagger": "/docs", "redoc": "/redoc", "openapi": "/openapi.json",
                 "metrics": "/metrics"},
        "auth": {
            "scheme": "Authorization: Bearer <token>",
            "tenant_header": "X-Tenant-ID (bắt buộc nếu token global '*')",
            "request_id_header": "X-Request-ID (tùy chọn, echo lại)",
            "admin_only": ["/admin/reload"],
            "rate_limit": f"{config.RATE_LIMIT_PER_MIN}/phút/tenant",
        },
        "endpoints": [
            {"method": "GET", "path": "/health", "auth": False, "desc": "liveness"},
            {"method": "GET", "path": "/ready", "auth": False, "desc": "readiness + trạng thái index"},
            {"method": "GET", "path": "/gpu", "auth": False, "desc": "provider ORT + VRAM"},
            {"method": "GET", "path": "/metrics", "auth": False, "desc": "Prometheus"},
            {"method": "POST", "path": "/v1/faces/detect", "auth": "client",
             "desc": "ảnh -> bbox + det_score", "body": "multipart file|url"},
            {"method": "POST", "path": "/v1/faces/embed", "auth": "client",
             "desc": "ảnh -> embedding 512-d để enroll", "body": "multipart file|url"},
            {"method": "POST", "path": "/v1/faces/search", "auth": "client",
             "desc": "ảnh -> person_id + score", "body": "multipart file|url",
             "query": {"top_k": config.TOP_K, "threshold": config.MATCH_THRESHOLD}},
            {"method": "POST", "path": "/admin/reload", "auth": "admin",
             "desc": "rebuild FAISS", "query": {"tenant_id": "optional; rỗng = tất cả"}},
            {"method": "GET", "path": "/v1/index/stats", "auth": "client|admin",
             "desc": "số person/vector đã index"},
        ],
        "enroll_flow": [
            "POST /v1/faces/embed  (ảnh chân dung)  -> faces[i].embedding",
            "POST {backend}/internal/tenants/{tid}/persons  {name, embeddings:[embedding]}",
            "POST /admin/reload?tenant_id={tid}",
            "POST /v1/faces/search  -> match.person_id",
        ],
        "notes": [
            "embedding đã L2-normalize; score = cosine similarity (0..1).",
            "match=null nghĩa là không có candidate nào >= threshold.",
            "gọi /ready tới khi ready=true trước khi bắn traffic thật.",
            f"model hiện tại: {config.INSIGHTFACE_MODEL}, dim={config.EMB_DIM}.",
        ],
    }


# ------------------------------- infra endpoints --------------------------- #

@app.get("/health", tags=["infra"], response_model=schemas.HealthResponse,
         summary="Liveness — process còn sống")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - STATE["started_at"], 1)}


@app.get("/ready", tags=["infra"], response_model=schemas.ReadyResponse,
         summary="Readiness — model + FAISS đã sẵn sàng",
         responses={503: {"model": schemas.ReadyResponse, "description": "Đang khởi động"}})
async def ready():
    body = {
        "ready": STATE["ready"],
        "detail": STATE["detail"],
        "provider": engine.provider,
        "model": config.INSIGHTFACE_MODEL,
        "indexed": store.stats(),
    }
    return JSONResponse(body, status_code=200 if STATE["ready"] else 503)


@app.get("/gpu", tags=["infra"], response_model=schemas.GpuResponse,
         summary="Provider ONNX Runtime + VRAM")
async def gpu():
    import onnxruntime as ort

    info = {"provider": engine.provider, "onnxruntime_providers": ort.get_available_providers()}
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        name, mt, mu, ut = (x.strip() for x in out.split(","))
        info |= {
            "name": name,
            "vram_total_mb": int(mt),
            "vram_used_mb": int(mu),
            "gpu_util_pct": int(ut),
        }
    except Exception as e:  # noqa: BLE001
        info["nvidia_smi"] = f"n/a: {e}"
    return info


@app.get("/metrics", tags=["infra"], summary="Prometheus metrics (LAN — nên firewall)")
async def prometheus_metrics():
    if not config.METRICS_ENABLED:
        raise HTTPException(404, "metrics disabled")
    metrics.refresh_gpu_gauges()
    metrics.refresh_index_gauges(store.stats())
    body, ctype = metrics.render()
    return Response(content=body, media_type=ctype)


# ------------------------------- face endpoints ---------------------------- #

@app.post(
    "/v1/faces/detect",
    tags=["faces"],
    response_model=schemas.DetectResponse,
    responses=COMMON_ERRORS,
    summary="Phát hiện khuôn mặt (bbox, không nhận dạng)",
)
async def faces_detect(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Ảnh upload (jpg/png/…)"),
    url: Optional[str] = Form(None, description="Hoặc URL ảnh http/https"),
):
    _guard_ready()
    require_tenant(auth)
    await _rate_check(auth)
    faces, ms = await _analyze(await _read_image(file, url), "detect")
    if config.METRICS_ENABLED:
        metrics.FACES_DETECTED.labels(auth.tenant).inc(len(faces))
    return {
        "request_id": auth.request_id,
        "tenant_id": auth.tenant,
        "count": len(faces),
        "faces": [{"bbox_xyxy": f["bbox_xyxy"], "det_score": f["det_score"]} for f in faces],
        "inference_ms": ms,
    }


@app.post(
    "/v1/faces/embed",
    tags=["faces"],
    response_model=schemas.EmbedResponse,
    responses=COMMON_ERRORS,
    summary="Trích embedding 512-d cho mỗi khuôn mặt (để enroll)",
)
async def faces_embed(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Ảnh chân dung (1 mặt là tốt nhất)"),
    url: Optional[str] = Form(None, description="Hoặc URL ảnh http/https"),
):
    """Kết quả `faces[i].embedding` (512 số, đã L2-norm) là dữ liệu gửi vào backend khi enroll."""
    _guard_ready()
    require_tenant(auth)
    await _rate_check(auth)
    faces, ms = await _analyze(await _read_image(file, url), "embed")
    if config.METRICS_ENABLED:
        metrics.FACES_DETECTED.labels(auth.tenant).inc(len(faces))
    return {
        "request_id": auth.request_id,
        "tenant_id": auth.tenant,
        "model": config.INSIGHTFACE_MODEL,
        "dim": config.EMB_DIM,
        "count": len(faces),
        "faces": [
            {
                "bbox_xyxy": f["bbox_xyxy"],
                "det_score": f["det_score"],
                "embedding": [round(float(x), 6) for x in f["embedding"].tolist()],
            }
            for f in faces
        ],
        "inference_ms": ms,
    }


@app.post(
    "/v1/faces/search",
    tags=["faces"],
    response_model=schemas.SearchResponse,
    responses=COMMON_ERRORS,
    summary="Nhận dạng — mỗi khuôn mặt → person_id + score",
)
async def faces_search(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Ảnh cần nhận dạng"),
    url: Optional[str] = Form(None, description="Hoặc URL ảnh http/https"),
    top_k: int = Query(config.TOP_K, ge=1, le=50, description="Số candidate trả về / mặt"),
    threshold: float = Query(
        config.MATCH_THRESHOLD, ge=0.0, le=1.0,
        description="Ngưỡng cosine để coi là 'match'. Thấp hơn = dễ match hơn (nhiều false positive).",
    ),
):
    _guard_ready()
    require_tenant(auth)
    await _rate_check(auth)
    idx = await store.ensure(auth.tenant)
    faces, ms = await _analyze(await _read_image(file, url), "search")

    results = []
    n_match = 0
    for f in faces:
        cands = idx.search(f["embedding"], top_k)
        match = cands[0] if cands and cands[0]["score"] >= threshold else None
        if match:
            n_match += 1
        results.append(
            {
                "bbox_xyxy": f["bbox_xyxy"],
                "det_score": f["det_score"],
                "match": match,
                "candidates": cands,
            }
        )

    if config.METRICS_ENABLED:
        metrics.FACES_DETECTED.labels(auth.tenant).inc(len(results))
        if n_match:
            metrics.MATCHES.labels(auth.tenant).inc(n_match)

    if config.POST_EVENTS:
        asyncio.create_task(
            backend.post_event(
                auth.tenant,
                "face_search",
                {
                    "request_id": auth.request_id,
                    "n_faces": len(results),
                    "matches": [r["match"] for r in results if r["match"]],
                },
            )
        )

    return {
        "request_id": auth.request_id,
        "tenant_id": auth.tenant,
        "count": len(results),
        "faces": results,
        "threshold": threshold,
        "inference_ms": ms,
        "index": {"persons": idx.n_persons, "vectors": idx.n_vectors},
    }


@app.post(
    "/admin/reload",
    tags=["admin"],
    response_model=schemas.ReloadResponse,
    responses=COMMON_ERRORS,
    summary="Rebuild FAISS index từ backend",
)
async def admin_reload(
    auth: Auth = Depends(auth_ctx),
    tenant_id: Optional[str] = Query(None, description="Bỏ trống = reload tất cả tenant"),
):
    if auth.role != "admin":
        raise HTTPException(403, "Cần token role=admin")
    if tenant_id:
        await store.reload(tenant_id)
        out = {"reloaded": [tenant_id], "stats": store.stats(tenant_id)}
    else:
        tids = await store.reload_all()
        out = {"reloaded": tids, "stats": store.stats()}
    if config.METRICS_ENABLED:
        metrics.refresh_index_gauges(store.stats())
    return out


@app.get(
    "/v1/index/stats",
    tags=["faces"],
    response_model=schemas.StatsResponse,
    responses=COMMON_ERRORS,
    summary="Số person / vector đã index",
)
async def index_stats(auth: Auth = Depends(auth_ctx)):
    if auth.role == "admin" and not auth.tenant:
        return {"stats": store.stats()}
    require_tenant(auth)
    await store.ensure(auth.tenant)
    return {"stats": store.stats(auth.tenant)}
