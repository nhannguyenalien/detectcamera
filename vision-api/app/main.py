"""
vision-api — nhận dạng bằng ảnh trên GPU, multi-tenant, FAISS in-RAM.

2 modality (bật/tắt qua env):
  - face   : InsightFace SCRFD + ArcFace  (nhiều mặt / ảnh)
  - product: DINOv2-S visual search       (1 ảnh = 1 sản phẩm)

Boot (gate /ready): load model(s) -> warmup GPU -> sync embeddings -> build FAISS -> ready.
Auth mọi /v1/* và /admin/*:  Authorization: Bearer <token>  + (X-Tenant-ID).
"""
import asyncio
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from . import clothing, config, metrics, net, schemas, tokens
from .backend import BackendClient
from .deps import Auth, RateLimiter, auth_ctx, require_tenant
from .engine import FaceEngine
from .index import IndexStore
from .body import BodyEngine
from .products import ProductEngine

STATE = {"ready": False, "detail": "starting", "started_at": time.time()}

backend = BackendClient()
gpu_sem = asyncio.Semaphore(config.GPU_CONCURRENCY)
limiter = RateLimiter(config.RATE_LIMIT_PER_MIN)

face_engine = FaceEngine() if config.ENABLE_FACE else None
product_engine = ProductEngine() if config.ENABLE_PRODUCTS else None
body_engine = BodyEngine() if config.ENABLE_BODY else None

face_store = IndexStore(backend, "get_face_embeddings", "persons", "person_id", config.EMB_DIM)
product_store = IndexStore(
    backend, "get_product_embeddings", "products", "product_id", config.PRODUCT_EMB_DIM
)
body_store = IndexStore(
    backend, "get_body_embeddings", "persons", "person_id", config.BODY_EMB_DIM
)

DESCRIPTION = """\
API nhận dạng bằng ảnh trên GPU, **multi-tenant**, FAISS in-RAM.

- **face** — InsightFace SCRFD + ArcFace. Nhiều mặt / ảnh.
- **product** — DINOv2-S visual search. **1 ảnh = 1 sản phẩm**, so khớp với catalog của tenant.

### Xác thực
Mọi `/v1/*` và `/admin/*`:

| Header | Bắt buộc | |
|---|---|---|
| `Authorization: Bearer <token>` | ✅ | token 1 tenant, hoặc global `*` |
| `X-Tenant-ID` | khi token global | chọn tenant |
| `X-Request-ID` | không | echo lại |

`role=admin` mới gọi `/admin/reload`. Rate limit theo tenant (429).

### Enroll (giống nhau cho cả 2)
```
POST /v1/{faces|products}/embed  (ảnh)  -> embedding
POST {backend}/internal/tenants/{tid}/{persons|products}  {name, embeddings:[emb]}
POST /admin/reload?modality={face|product}&tenant_id={tid}
POST /v1/{faces|products}/search  -> match.{person_id|product_id} + score
```

### Ảnh đầu vào
`multipart/form-data`: `file` (upload) hoặc `url` (http/https, chặn IP nội bộ, không follow redirect lạ).
Giới hạn `VISION_MAX_IMAGE_BYTES` (20MB), `VISION_MAX_IMAGE_PIXELS`.

`GET /metrics` Prometheus · `GET /health` liveness · `GET /ready` readiness.
"""

_LLMS_TXT = """\
# vision-api - GPU image recognition, multi-tenant

Full machine-readable manifest: GET /
OpenAPI: GET /openapi.json  |  Swagger: GET /docs  |  ReDoc: GET /redoc

## Auth (moi /v1/* va /admin/*)
Authorization: Bearer <token>
X-Tenant-ID: <tenant>       # bat buoc neu token la global "*"
X-Request-ID: <optional>    # echo lai trong response
role=admin moi goi duoc /admin/reload. Rate limit theo tenant -> HTTP 429.

## Anh dau vao
multipart/form-data: field "file" (upload) HOAC "url" (http/https; chan IP noi bo; khong follow redirect la).

## Endpoint
GET  /                         manifest JSON day du (doc de lay param, threshold, dim)
GET  /health                   liveness
GET  /ready                    readiness - poll toi ready=true truoc khi ban traffic
GET  /gpu                      ORT provider + VRAM
POST /v1/faces/detect          anh -> [bbox, det_score]
POST /v1/faces/embed           anh -> face embedding (L2-normalized) de enroll
POST /v1/faces/search          ?top_k&threshold -> moi mat: person_id + score
POST /v1/products/embed        1 anh -> product embedding
POST /v1/products/search       ?top_k&threshold -> product_id + score
POST /v1/body/embed           anh crop toan than -> body ReID embedding
POST /v1/body/search          ?top_k&threshold -> person_id + score
POST /v1/body/attributes      anh -> mau ao/quan (loc nhanh, KHONG phai ID)
POST /v1/persons/identify     anh crop 1 nguoi -> fusion face+body -> person_id + confidence
GET  /v1/index/stats           kich thuoc index face
GET  /v1/products/index/stats  kich thuoc index product
GET  /v1/body/index/stats      kich thuoc index body
POST /admin/reload             ?modality=face|product|body|all&tenant_id=  rebuild FAISS (admin)

## Luong enroll (giong nhau cho face va product)
1. POST /v1/<faces|products>/embed  (anh)  -> embedding
2. POST <backend>/internal/tenants/<tid>/<persons|products>  {name, embeddings:[emb]}
3. POST /admin/reload?modality=<face|product>&tenant_id=<tid>
4. POST /v1/<faces|products>/search  -> match id + score

## Ghi chu
- embedding da L2-normalize; score = cosine similarity (0..1).
- match=null nghia la khong co candidate nao >= threshold.
- 1 anh product = 1 product.
- body ReID: chi 1 tin hieu; fusion voi face o /v1/persons/identify. Quan ao khong ben (doi do -> giam).
"""


TAGS = [
    {"name": "infra", "description": "Liveness / readiness / GPU / metrics. Không auth."},
    {"name": "faces", "description": "Detect / embed / search khuôn mặt."},
    {"name": "products", "description": "Embed / search sản phẩm (1 ảnh = 1 sp)."},
    {"name": "body", "description": "Person ReID toàn thân — embed / search / attributes / identify."},
    {"name": "admin", "description": "Quản trị index. Token role=admin."},
    {"name": "meta", "description": "Manifest cho client / AI agent."},
]

COMMON_ERRORS = {
    400: {"model": schemas.ErrorResponse, "description": "Ảnh/URL không hợp lệ"},
    401: {"model": schemas.ErrorResponse, "description": "Thiếu / sai token"},
    403: {"model": schemas.ErrorResponse, "description": "Sai tenant hoặc thiếu quyền admin"},
    404: {"model": schemas.ErrorResponse, "description": "Modality bị tắt"},
    429: {"model": schemas.ErrorResponse, "description": "Vượt rate limit"},
    503: {"model": schemas.ErrorResponse, "description": "Service chưa `ready`"},
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    loop = asyncio.get_event_loop()
    _refresher = None
    try:
        if face_engine:
            STATE["detail"] = "loading face model"
            await loop.run_in_executor(None, face_engine.load)
            await loop.run_in_executor(None, face_engine.warmup)
        if product_engine:
            STATE["detail"] = "loading product model"
            await loop.run_in_executor(None, product_engine.load)
            await loop.run_in_executor(None, product_engine.warmup)
        if body_engine:
            STATE["detail"] = "loading body reid model"
            await loop.run_in_executor(None, body_engine.load)
            await loop.run_in_executor(None, body_engine.warmup)

        if config.PREFETCH_ON_START:
            STATE["detail"] = "sync embeddings"
            for enabled, st, mod in (
                (config.ENABLE_FACE, face_store, "face"),
                (config.ENABLE_PRODUCTS, product_store, "product"),
                (config.ENABLE_BODY, body_store, "body"),
            ):
                if not enabled:
                    continue
                try:
                    tids = await st.reload_all()
                    metrics.refresh_index_gauges(st.stats(), mod)
                    STATE["detail"] = f"indexed {mod}={tids}"
                except Exception as e:  # noqa: BLE001 - vẫn ready, lazy-load sau
                    STATE["detail"] = f"{mod} prefetch failed, lazy later: {e}"

        if config.TOKENS_FROM_BACKEND:
            try:
                n = await tokens.refresh_once(backend)
                print(f"[boot] loaded {n} dynamic tokens từ backend")
            except Exception as e:  # noqa: BLE001
                print(f"[boot] token fetch failed (dùng env tạm): {e}")
            _refresher = asyncio.create_task(tokens.refresher(backend))

        STATE["ready"] = True
        if "fail" not in STATE["detail"]:
            STATE["detail"] = "ready"
        metrics.READY.set(1)
        print(f"[boot] READY face={bool(face_engine)} product={bool(product_engine)} {STATE['detail']}")
    except Exception as e:  # noqa: BLE001
        STATE["ready"] = False
        STATE["detail"] = f"startup error: {e}"
        metrics.READY.set(0)
        print(f"[boot] FAILED: {e}")
        raise
    yield
    if _refresher:
        _refresher.cancel()
    await backend.close()


app = FastAPI(
    title="vision-api",
    version="1.2.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    contact={"name": "vision-stack"},
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


async def _analyze_faces(raw: bytes, kind: str):
    loop = asyncio.get_event_loop()
    try:
        bgr = await loop.run_in_executor(None, face_engine.decode, raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Ảnh không hợp lệ: {e}")
    async with gpu_sem:
        faces, ms = await loop.run_in_executor(None, face_engine.analyze, bgr)
    if config.METRICS_ENABLED:
        metrics.INFERENCE.labels(kind).observe(ms / 1000.0)
    return faces, ms


async def _embed_product(raw: bytes, kind: str):
    loop = asyncio.get_event_loop()
    async with gpu_sem:
        try:
            vec, ms = await loop.run_in_executor(None, product_engine.embed, raw)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Ảnh không hợp lệ: {e}")
    if config.METRICS_ENABLED:
        metrics.INFERENCE.labels(kind).observe(ms / 1000.0)
    return vec, ms


async def _embed_body(raw: bytes, kind: str):
    loop = asyncio.get_event_loop()
    async with gpu_sem:
        try:
            vec, ms = await loop.run_in_executor(None, body_engine.embed, raw)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Ảnh không hợp lệ: {e}")
    if config.METRICS_ENABLED:
        metrics.INFERENCE.labels(kind).observe(ms / 1000.0)
    return vec, ms


def _guard_ready() -> None:
    if not STATE["ready"]:
        raise HTTPException(503, f"Service chưa sẵn sàng: {STATE['detail']}")


def _guard_face() -> None:
    if not config.ENABLE_FACE or face_engine is None:
        raise HTTPException(404, "modality 'face' đang tắt (VISION_ENABLE_FACE=false)")


def _guard_product() -> None:
    if not config.ENABLE_PRODUCTS or product_engine is None:
        raise HTTPException(404, "modality 'product' đang tắt (VISION_ENABLE_PRODUCTS=false)")


def _guard_body() -> None:
    if not config.ENABLE_BODY or body_engine is None:
        raise HTTPException(404, "modality 'body' đang tắt (VISION_ENABLE_BODY=false)")


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
    """Đọc trước khi gọi API: endpoint, cách auth, luồng enroll."""
    eps = [
        {"method": "GET", "path": "/health", "auth": False, "desc": "liveness"},
        {"method": "GET", "path": "/ready", "auth": False, "desc": "readiness + trạng thái index"},
        {"method": "GET", "path": "/gpu", "auth": False, "desc": "provider ORT + VRAM"},
        {"method": "GET", "path": "/metrics", "auth": False, "desc": "Prometheus"},
        {"method": "POST", "path": "/admin/reload", "auth": "admin",
         "desc": "rebuild FAISS", "query": {"modality": "face|product|all", "tenant_id": "optional"}},
    ]
    if config.ENABLE_FACE:
        eps += [
            {"method": "POST", "path": "/v1/faces/detect", "auth": "client", "body": "multipart file|url"},
            {"method": "POST", "path": "/v1/faces/embed", "auth": "client", "body": "multipart file|url",
             "desc": "ảnh -> embedding 512-d"},
            {"method": "POST", "path": "/v1/faces/search", "auth": "client", "body": "multipart file|url",
             "query": {"top_k": config.TOP_K, "threshold": config.MATCH_THRESHOLD},
             "desc": "ảnh -> person_id + score"},
            {"method": "GET", "path": "/v1/index/stats", "auth": "client|admin"},
        ]
    if config.ENABLE_PRODUCTS:
        eps += [
            {"method": "POST", "path": "/v1/products/embed", "auth": "client", "body": "multipart file|url",
             "desc": f"1 ảnh -> embedding {config.PRODUCT_EMB_DIM}-d"},
            {"method": "POST", "path": "/v1/products/search", "auth": "client", "body": "multipart file|url",
             "query": {"top_k": config.PRODUCT_TOP_K, "threshold": config.PRODUCT_MATCH_THRESHOLD},
             "desc": "1 ảnh -> product_id + score"},
            {"method": "GET", "path": "/v1/products/index/stats", "auth": "client|admin"},
        ]
    if config.ENABLE_BODY:
        eps += [
            {"method": "POST", "path": "/v1/body/embed", "auth": "client", "body": "multipart file|url",
             "desc": "crop toàn thân -> body ReID embedding"},
            {"method": "POST", "path": "/v1/body/search", "auth": "client", "body": "multipart file|url",
             "query": {"top_k": config.BODY_TOP_K, "threshold": config.BODY_MATCH_THRESHOLD},
             "desc": "crop toàn thân -> person_id + score"},
            {"method": "POST", "path": "/v1/body/attributes", "auth": "client", "body": "multipart file|url",
             "desc": "màu áo/quần (lọc nhanh, KHÔNG phải ID)"},
            {"method": "POST", "path": "/v1/persons/identify", "auth": "client", "body": "multipart file|url",
             "desc": "fusion face+body -> person_id + confidence"},
            {"method": "GET", "path": "/v1/body/index/stats", "auth": "client|admin"},
        ]
    return {
        "service": "vision-api",
        "version": app.version,
        "ready_url": "/ready",
        "modalities": {"face": config.ENABLE_FACE, "product": config.ENABLE_PRODUCTS, "body": config.ENABLE_BODY},
        "docs": {"swagger": "/docs", "redoc": "/redoc", "openapi": "/openapi.json", "metrics": "/metrics"},
        "auth": {
            "scheme": "Authorization: Bearer <token>",
            "tenant_header": "X-Tenant-ID (bắt buộc nếu token global '*')",
            "request_id_header": "X-Request-ID (tùy chọn, echo lại)",
            "admin_only": ["/admin/reload"],
            "rate_limit": f"{config.RATE_LIMIT_PER_MIN}/phút/tenant",
        },
        "endpoints": eps,
        "enroll_flow": [
            "POST /v1/{faces|products}/embed  (ảnh)  -> embedding",
            "POST {backend}/internal/tenants/{tid}/{persons|products}  {name, embeddings:[emb]}",
            "POST /admin/reload?modality={face|product}&tenant_id={tid}",
            "POST /v1/{faces|products}/search  -> match id + score",
        ],
        "notes": [
            "embedding đã L2-normalize; score = cosine (0..1).",
            "match=null = không candidate nào >= threshold.",
            "poll /ready tới ready=true trước khi bắn traffic.",
            f"face: {config.INSIGHTFACE_MODEL} dim={config.EMB_DIM}. "
            f"product: {config.PRODUCT_MODEL} dim={config.PRODUCT_EMB_DIM}, 1 ảnh = 1 sp.",
            "body: 1 tín hiệu ReID toàn thân; fusion với face ở /v1/persons/identify. "
            "Quần áo mạnh nhưng KHÔNG bền — đổi đồ thì tin cậy giảm.",
        ],
    }


@app.get("/llms.txt", tags=["meta"], include_in_schema=False,
         summary="Huong dan ngan cho LLM / AI agent (text/plain)")
async def llms_txt():
    """Ban text gon cho AI agent - doc truoc khi tich hop."""
    return Response(_LLMS_TXT, media_type="text/plain; charset=utf-8")


# ------------------------------- infra ----------------------------------- #

@app.get("/health", tags=["infra"], response_model=schemas.HealthResponse,
         summary="Liveness")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - STATE["started_at"], 1)}


@app.get("/ready", tags=["infra"], response_model=schemas.ReadyResponse,
         summary="Readiness — model + FAISS sẵn sàng",
         responses={503: {"model": schemas.ReadyResponse, "description": "Đang khởi động"}})
async def ready():
    body = {
        "ready": STATE["ready"],
        "detail": STATE["detail"],
        "modalities": {
            "face": {
                "enabled": config.ENABLE_FACE,
                "provider": face_engine.provider if face_engine else None,
                "model": config.INSIGHTFACE_MODEL,
                "indexed": face_store.stats(),
            },
            "product": {
                "enabled": config.ENABLE_PRODUCTS,
                "provider": product_engine.provider if product_engine else None,
                "model": config.PRODUCT_MODEL,
                "indexed": product_store.stats(),
            },
            "body": {
                "enabled": config.ENABLE_BODY,
                "provider": body_engine.provider if body_engine else None,
                "model": config.BODY_MODEL,
                "indexed": body_store.stats(),
            },
        },
    }
    return JSONResponse(body, status_code=200 if STATE["ready"] else 503)


@app.get("/gpu", tags=["infra"], response_model=schemas.GpuResponse,
         summary="Provider ONNX Runtime + VRAM")
async def gpu():
    import onnxruntime as ort

    prov = (face_engine.provider if face_engine else None) or (
        product_engine.provider if product_engine else None
    )
    info = {"provider": prov, "onnxruntime_providers": ort.get_available_providers()}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        name, mt, mu, ut = (x.strip() for x in out.split(","))
        info |= {"name": name, "vram_total_mb": int(mt), "vram_used_mb": int(mu), "gpu_util_pct": int(ut)}
    except Exception as e:  # noqa: BLE001
        info["nvidia_smi"] = f"n/a: {e}"
    return info


@app.get("/metrics", tags=["infra"], summary="Prometheus metrics (LAN — nên firewall)")
async def prometheus_metrics():
    if not config.METRICS_ENABLED:
        raise HTTPException(404, "metrics disabled")
    metrics.refresh_gpu_gauges()
    if config.ENABLE_FACE:
        metrics.refresh_index_gauges(face_store.stats(), "face")
    if config.ENABLE_PRODUCTS:
        metrics.refresh_index_gauges(product_store.stats(), "product")
    body, ctype = metrics.render()
    return Response(content=body, media_type=ctype)


# ------------------------------- faces ----------------------------------- #

@app.post("/v1/faces/detect", tags=["faces"], response_model=schemas.DetectResponse,
          responses=COMMON_ERRORS, summary="Phát hiện khuôn mặt (bbox)")
async def faces_detect(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
):
    _guard_ready(); _guard_face(); require_tenant(auth)
    await _rate_check(auth)
    faces, ms = await _analyze_faces(await _read_image(file, url), "face_detect")
    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "face").inc(len(faces))
    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant, "count": len(faces),
        "faces": [{"bbox_xyxy": f["bbox_xyxy"], "det_score": f["det_score"]} for f in faces],
        "inference_ms": ms,
    }


@app.post("/v1/faces/embed", tags=["faces"], response_model=schemas.EmbedResponse,
          responses=COMMON_ERRORS, summary="Embedding 512-d / mặt (để enroll)")
async def faces_embed(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
):
    _guard_ready(); _guard_face(); require_tenant(auth)
    await _rate_check(auth)
    faces, ms = await _analyze_faces(await _read_image(file, url), "face_embed")
    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "face").inc(len(faces))
    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "model": config.INSIGHTFACE_MODEL, "dim": config.EMB_DIM, "count": len(faces),
        "faces": [
            {"bbox_xyxy": f["bbox_xyxy"], "det_score": f["det_score"],
             "embedding": [round(float(x), 6) for x in f["embedding"].tolist()]}
            for f in faces
        ],
        "inference_ms": ms,
    }


@app.post("/v1/faces/search", tags=["faces"], response_model=schemas.SearchResponse,
          responses=COMMON_ERRORS, summary="Nhận dạng mặt → person_id + score")
async def faces_search(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
    top_k: int = Query(config.TOP_K, ge=1, le=50),
    threshold: float = Query(config.MATCH_THRESHOLD, ge=0.0, le=1.0),
):
    _guard_ready(); _guard_face(); require_tenant(auth)
    await _rate_check(auth)
    idx = await face_store.ensure(auth.tenant)
    faces, ms = await _analyze_faces(await _read_image(file, url), "face_search")

    results, n_match = [], 0
    for f in faces:
        cands = idx.search(f["embedding"], top_k)
        match = cands[0] if cands and cands[0]["score"] >= threshold else None
        if match:
            n_match += 1
        results.append({"bbox_xyxy": f["bbox_xyxy"], "det_score": f["det_score"],
                        "match": match, "candidates": cands})

    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "face").inc(len(results))
        if n_match:
            metrics.MATCHES.labels(auth.tenant, "face").inc(n_match)
    if config.POST_EVENTS:
        asyncio.create_task(backend.post_event(auth.tenant, "face_search", {
            "request_id": auth.request_id, "n_faces": len(results),
            "matches": [r["match"] for r in results if r["match"]]}))

    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant, "count": len(results),
        "faces": results, "threshold": threshold, "inference_ms": ms,
        "index": {"persons": idx.n_persons, "vectors": idx.n_vectors},
    }


@app.get("/v1/index/stats", tags=["faces"], response_model=schemas.StatsResponse,
         responses=COMMON_ERRORS, summary="Số person / vector đã index (face)")
async def index_stats(auth: Auth = Depends(auth_ctx)):
    _guard_face()
    if auth.role == "admin" and not auth.tenant:
        return {"stats": face_store.stats()}
    require_tenant(auth)
    await face_store.ensure(auth.tenant)
    return {"stats": face_store.stats(auth.tenant)}


# ------------------------------- products -------------------------------- #

@app.post("/v1/products/embed", tags=["products"], response_model=schemas.ProductEmbedResponse,
          responses=COMMON_ERRORS, summary="1 ảnh → embedding sản phẩm (để enroll)")
async def products_embed(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Ảnh 1 sản phẩm, cận cảnh"),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
):
    """`embedding` (đã L2-norm) là dữ liệu gửi vào backend khi enroll sản phẩm."""
    _guard_ready(); _guard_product(); require_tenant(auth)
    await _rate_check(auth)
    vec, ms = await _embed_product(await _read_image(file, url), "product_embed")
    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "product").inc(1)
    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "model": config.PRODUCT_MODEL, "dim": config.PRODUCT_EMB_DIM,
        "embedding": [round(float(x), 6) for x in vec.tolist()],
        "inference_ms": ms,
    }


@app.post("/v1/products/search", tags=["products"], response_model=schemas.ProductSearchResponse,
          responses=COMMON_ERRORS, summary="1 ảnh → product_id + score")
async def products_search(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Ảnh sản phẩm cần nhận diện"),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
    top_k: int = Query(config.PRODUCT_TOP_K, ge=1, le=50),
    threshold: float = Query(config.PRODUCT_MATCH_THRESHOLD, ge=0.0, le=1.0,
                             description="Ngưỡng cosine coi là match. Calibrate trên catalog thật."),
):
    _guard_ready(); _guard_product(); require_tenant(auth)
    await _rate_check(auth)
    idx = await product_store.ensure(auth.tenant)
    vec, ms = await _embed_product(await _read_image(file, url), "product_search")

    cands = [
        {"product_id": c["person_id"], "name": c["name"], "score": c["score"]}
        for c in idx.search(vec, top_k)
    ]
    match = cands[0] if cands and cands[0]["score"] >= threshold else None

    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "product").inc(1)
        if match:
            metrics.MATCHES.labels(auth.tenant, "product").inc(1)
    if config.POST_EVENTS:
        asyncio.create_task(backend.post_event(auth.tenant, "product_search", {
            "request_id": auth.request_id, "match": match}))

    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "match": match, "candidates": cands, "threshold": threshold, "inference_ms": ms,
        "index": {"products": idx.n_persons, "vectors": idx.n_vectors},
    }


@app.get("/v1/products/index/stats", tags=["products"], response_model=schemas.StatsResponse,
         responses=COMMON_ERRORS, summary="Số product / vector đã index")
async def products_index_stats(auth: Auth = Depends(auth_ctx)):
    _guard_product()
    if auth.role == "admin" and not auth.tenant:
        return {"stats": _rename_products(product_store.stats())}
    require_tenant(auth)
    await product_store.ensure(auth.tenant)
    return {"stats": _rename_products(product_store.stats(auth.tenant))}


def _rename_products(stats: dict) -> dict:
    return {t: {"products": s.get("persons", 0), "vectors": s.get("vectors", 0)} for t, s in stats.items()}


# ------------------------------- body (person ReID) -------------------- #

@app.post("/v1/body/embed", tags=["body"], response_model=schemas.BodyEmbedResponse,
          responses=COMMON_ERRORS, summary="Crop toàn thân → body ReID embedding (để enroll)")
async def body_embed(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Crop 1 người, toàn thân"),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
):
    _guard_ready(); _guard_body(); require_tenant(auth)
    await _rate_check(auth)
    vec, ms = await _embed_body(await _read_image(file, url), "body_embed")
    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "body").inc(1)
    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "model": config.BODY_MODEL, "dim": len(vec),
        "embedding": [round(float(x), 6) for x in vec.tolist()],
        "inference_ms": ms,
    }


@app.post("/v1/body/search", tags=["body"], response_model=schemas.BodySearchResponse,
          responses=COMMON_ERRORS, summary="Crop toàn thân → person_id + score")
async def body_search(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
    top_k: int = Query(config.BODY_TOP_K, ge=1, le=50),
    threshold: float = Query(config.BODY_MATCH_THRESHOLD, ge=0.0, le=1.0,
                             description="Ngưỡng cosine. Calibrate trên dữ liệu thật."),
):
    _guard_ready(); _guard_body(); require_tenant(auth)
    await _rate_check(auth)
    idx = await body_store.ensure(auth.tenant)
    vec, ms = await _embed_body(await _read_image(file, url), "body_search")
    cands = idx.search(vec, top_k)
    match = cands[0] if cands and cands[0]["score"] >= threshold else None
    if config.METRICS_ENABLED:
        metrics.OBJECTS_DETECTED.labels(auth.tenant, "body").inc(1)
        if match:
            metrics.MATCHES.labels(auth.tenant, "body").inc(1)
    if config.POST_EVENTS:
        asyncio.create_task(backend.post_event(auth.tenant, "body_search", {
            "request_id": auth.request_id, "match": match}))
    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "match": match, "candidates": cands, "threshold": threshold, "inference_ms": ms,
        "index": {"persons": idx.n_persons, "vectors": idx.n_vectors},
    }


@app.get("/v1/body/index/stats", tags=["body"], response_model=schemas.StatsResponse,
         responses=COMMON_ERRORS, summary="Số person / vector đã index (body)")
async def body_index_stats(auth: Auth = Depends(auth_ctx)):
    _guard_body()
    if auth.role == "admin" and not auth.tenant:
        return {"stats": body_store.stats()}
    require_tenant(auth)
    await body_store.ensure(auth.tenant)
    return {"stats": body_store.stats(auth.tenant)}


@app.post("/v1/body/attributes", tags=["body"], response_model=schemas.ClothingAttributes,
          responses=COMMON_ERRORS, summary="Ảnh → màu áo / quần (lọc nhanh, KHÔNG phải ID)")
async def body_attributes(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
):
    _guard_ready(); _guard_body(); require_tenant(auth)
    await _rate_check(auth)
    raw = await _read_image(file, url)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, clothing.attributes, raw)


@app.post("/v1/persons/identify", tags=["body"], response_model=schemas.PersonIdentifyResponse,
          responses=COMMON_ERRORS,
          summary="Crop 1 người → fusion face + body → person_id + confidence")
async def persons_identify(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None, description="Crop 1 người (đã detect person)"),
    url: Optional[str] = Form(None, description="URL ảnh http/https"),
    top_k: int = Query(5, ge=1, le=50),
    face_threshold: float = Query(config.MATCH_THRESHOLD, ge=0.0, le=1.0),
    body_threshold: float = Query(config.BODY_MATCH_THRESHOLD, ge=0.0, le=1.0),
):
    """Fusion nhiều tín hiệu trên cùng 1 crop. Không thấy mặt → chỉ body; thấy mặt rõ → ưu tiên face.

    clothing_score = null ở phase 1 (chờ backend lưu attributes gallery).
    """
    _guard_ready(); require_tenant(auth)
    if not (config.ENABLE_FACE or config.ENABLE_BODY):
        raise HTTPException(404, "cần bật modality face hoặc body")
    await _rate_check(auth)
    raw = await _read_image(file, url)
    loop = asyncio.get_event_loop()

    agg: dict = {}
    face_visible = False
    total_ms = 0.0

    if config.ENABLE_FACE and face_engine is not None:
        faces, fms = await _analyze_faces(raw, "identify_face")
        total_ms += fms
        fidx = await face_store.ensure(auth.tenant)
        faces = sorted(
            faces,
            key=lambda f: (f["bbox_xyxy"][2] - f["bbox_xyxy"][0]) * (f["bbox_xyxy"][3] - f["bbox_xyxy"][1]),
            reverse=True,
        )
        if faces:
            face_visible = True
            for c in fidx.search(faces[0]["embedding"], top_k):
                e = agg.setdefault(c["person_id"], {
                    "person_id": c["person_id"], "name": c["name"],
                    "face_score": None, "body_score": None})
                e["face_score"] = max(e["face_score"] or 0.0, c["score"])

    if config.ENABLE_BODY and body_engine is not None:
        bvec, bms = await _embed_body(raw, "identify_body")
        total_ms += bms
        bidx = await body_store.ensure(auth.tenant)
        for c in bidx.search(bvec, top_k):
            e = agg.setdefault(c["person_id"], {
                "person_id": c["person_id"], "name": c["name"],
                "face_score": None, "body_score": None})
            e["body_score"] = max(e["body_score"] or 0.0, c["score"])

    attrs = None
    if config.ENABLE_BODY:
        attrs = await loop.run_in_executor(None, clothing.attributes, raw)

    def fuse(e: dict) -> float:
        f = e["face_score"] or 0.0
        b = e["body_score"] or 0.0
        if e["face_score"] is not None and f >= face_threshold:
            return round(0.65 * f + 0.35 * b, 4)
        if e["body_score"] is not None:
            if e["face_score"] is not None:
                return round(0.25 * f + 0.75 * b, 4)
            return round(b, 4)
        return round(f, 4)

    cands = []
    for e in agg.values():
        cands.append({
            **e,
            "confidence": fuse(e),
            "clothing_score": None,
            "sources": [name for name, key in (("face", "face_score"), ("body", "body_score"))
                        if e[key] is not None],
        })
    cands.sort(key=lambda x: -x["confidence"])
    cands = cands[:top_k]

    best = None
    if cands:
        c0 = cands[0]
        if (c0["face_score"] or 0.0) >= face_threshold or (c0["body_score"] or 0.0) >= body_threshold:
            best = c0

    if config.POST_EVENTS:
        asyncio.create_task(backend.post_event(auth.tenant, "person_identify", {
            "request_id": auth.request_id, "match": best, "face_visible": face_visible}))

    return {
        "request_id": auth.request_id, "tenant_id": auth.tenant,
        "match": best, "candidates": cands, "attributes": attrs,
        "face_visible": face_visible, "inference_ms": round(total_ms, 1),
    }


# ------------------------------- admin --------------------------------- #

@app.post("/admin/reload", tags=["admin"], response_model=schemas.ReloadResponse,
          responses=COMMON_ERRORS, summary="Rebuild FAISS từ backend")
async def admin_reload(
    auth: Auth = Depends(auth_ctx),
    modality: str = Query("all", pattern="^(face|product|body|all)$"),
    tenant_id: Optional[str] = Query(None, description="Bỏ trống = tất cả tenant"),
):
    if auth.role != "admin":
        raise HTTPException(403, "Cần token role=admin")
    targets = []
    if modality in ("face", "all") and config.ENABLE_FACE:
        targets.append((face_store, "face"))
    if modality in ("product", "all") and config.ENABLE_PRODUCTS:
        targets.append((product_store, "product"))
    if modality in ("body", "all") and config.ENABLE_BODY:
        targets.append((body_store, "body"))

    out: dict = {"reloaded": {}, "stats": {}}
    for st, mod in targets:
        if tenant_id:
            await st.reload(tenant_id)
            out["reloaded"][mod] = [tenant_id]
            out["stats"][mod] = st.stats(tenant_id)
        else:
            tids = await st.reload_all()
            out["reloaded"][mod] = tids
            out["stats"][mod] = st.stats()
        if config.METRICS_ENABLED:
            metrics.refresh_index_gauges(st.stats(), mod)
    return out
