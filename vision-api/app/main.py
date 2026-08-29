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

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from . import config
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
        print(f"[boot] READY provider={engine.provider} {STATE['detail']}")
    except Exception as e:  # noqa: BLE001
        STATE["ready"] = False
        STATE["detail"] = f"startup error: {e}"
        print(f"[boot] FAILED: {e}")
        raise
    yield
    await backend.close()


app = FastAPI(title="vision-api (face)", version="1.0.0", lifespan=lifespan)


# ------------------------------- helpers ------------------------------------- #

async def _read_image(file: Optional[UploadFile], url: Optional[str]) -> bytes:
    if file is not None:
        raw = await file.read()
    elif url:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            raw = r.content
    else:
        raise HTTPException(422, "Cần 'file' (multipart) hoặc 'url'")
    if len(raw) > config.MAX_IMAGE_BYTES:
        raise HTTPException(413, "Ảnh quá lớn")
    return raw


async def _analyze(raw: bytes):
    loop = asyncio.get_event_loop()
    bgr = await loop.run_in_executor(None, engine.decode, raw)
    async with gpu_sem:
        faces, ms = await loop.run_in_executor(None, engine.analyze, bgr)
    return faces, ms


def _guard_ready() -> None:
    if not STATE["ready"]:
        raise HTTPException(503, f"Service chưa sẵn sàng: {STATE['detail']}")


# ------------------------------- infra endpoints --------------------------- #

@app.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - STATE["started_at"], 1)}


@app.get("/ready")
async def ready():
    body = {
        "ready": STATE["ready"],
        "detail": STATE["detail"],
        "provider": engine.provider,
        "model": config.INSIGHTFACE_MODEL,
        "indexed": store.stats(),
    }
    return JSONResponse(body, status_code=200 if STATE["ready"] else 503)


@app.get("/gpu")
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


# ------------------------------- face endpoints ---------------------------- #

@app.post("/v1/faces/detect")
async def faces_detect(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    _guard_ready()
    require_tenant(auth)
    await limiter.check(auth.tenant)
    faces, ms = await _analyze(await _read_image(file, url))
    return {
        "request_id": auth.request_id,
        "tenant_id": auth.tenant,
        "count": len(faces),
        "faces": [{"bbox_xyxy": f["bbox_xyxy"], "det_score": f["det_score"]} for f in faces],
        "inference_ms": ms,
    }


@app.post("/v1/faces/embed")
async def faces_embed(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """Trả embedding 512-d (đã L2-norm) cho mỗi mặt — dùng để enroll vào backend."""
    _guard_ready()
    require_tenant(auth)
    await limiter.check(auth.tenant)
    faces, ms = await _analyze(await _read_image(file, url))
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


@app.post("/v1/faces/search")
async def faces_search(
    auth: Auth = Depends(auth_ctx),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    top_k: int = Query(config.TOP_K, ge=1, le=50),
    threshold: float = Query(config.MATCH_THRESHOLD, ge=0.0, le=1.0),
):
    _guard_ready()
    require_tenant(auth)
    await limiter.check(auth.tenant)
    idx = await store.ensure(auth.tenant)
    faces, ms = await _analyze(await _read_image(file, url))

    results = []
    for f in faces:
        cands = idx.search(f["embedding"], top_k)
        match = cands[0] if cands and cands[0]["score"] >= threshold else None
        results.append(
            {
                "bbox_xyxy": f["bbox_xyxy"],
                "det_score": f["det_score"],
                "match": match,
                "candidates": cands,
            }
        )

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


@app.post("/admin/reload")
async def admin_reload(
    auth: Auth = Depends(auth_ctx),
    tenant_id: Optional[str] = Query(None, description="bỏ trống = reload tất cả tenant"),
):
    if auth.role != "admin":
        raise HTTPException(403, "Cần token role=admin")
    if tenant_id:
        await store.reload(tenant_id)
        return {"reloaded": [tenant_id], "stats": store.stats(tenant_id)}
    tids = await store.reload_all()
    return {"reloaded": tids, "stats": store.stats()}


@app.get("/v1/index/stats")
async def index_stats(auth: Auth = Depends(auth_ctx)):
    if auth.role == "admin" and not auth.tenant:
        return {"stats": store.stats()}
    require_tenant(auth)
    await store.ensure(auth.tenant)
    return {"stats": store.stats(auth.tenant)}
