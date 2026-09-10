"""Pydantic models — để OpenAPI (/docs, /openapi.json) có schema + ví dụ đầy đủ."""
from typing import Optional

from pydantic import BaseModel, Field


class FaceBox(BaseModel):
    bbox_xyxy: list[float] = Field(
        ..., description="Bounding box [x1, y1, x2, y2] theo pixel ảnh gốc.",
        examples=[[913.4, 96.3, 1057.1, 276.2]],
    )
    det_score: float = Field(
        ..., description="Độ tin cậy của face detector (0..1).", examples=[0.8714]
    )


class DetectResponse(BaseModel):
    request_id: str = Field(..., examples=["req_f6c383fddaf74d09"])
    tenant_id: str = Field(..., examples=["t_demo"])
    count: int = Field(..., description="Số khuôn mặt tìm thấy.", examples=[2])
    faces: list[FaceBox]
    inference_ms: float = Field(..., description="Thời gian suy luận GPU (ms).", examples=[41.2])


class EmbedFace(FaceBox):
    embedding: list[float] = Field(
        ...,
        description="Vector nhận dạng 512 chiều, ĐÃ L2-normalize (norm=1). "
        "Dùng làm dữ liệu enroll gửi vào backend.",
        min_length=512,
        max_length=512,
        examples=[[0.0299, 0.0189, -0.0228, -0.0827, "...(508 số nữa)"]],
    )


class EmbedResponse(BaseModel):
    request_id: str
    tenant_id: str
    model: str = Field(..., description="Tên model pack InsightFace.", examples=["buffalo_l"])
    dim: int = Field(512, examples=[512])
    count: int
    faces: list[EmbedFace]
    inference_ms: float


class Candidate(BaseModel):
    person_id: str = Field(..., examples=["p_73a70bd14145"])
    name: Optional[str] = Field(None, examples=["Zidane"])
    score: float = Field(
        ..., description="Cosine similarity với embedding truy vấn (0..1, cao = giống).",
        examples=[1.0],
    )


class SearchFace(FaceBox):
    match: Optional[Candidate] = Field(
        None,
        description="Candidate top-1 NẾU score >= threshold, ngược lại null (không nhận ra).",
    )
    candidates: list[Candidate] = Field(
        ..., description="Tối đa top_k candidate, giảm dần theo score (đã gộp trùng person)."
    )


class SearchIndexInfo(BaseModel):
    persons: int = Field(..., examples=[2])
    vectors: int = Field(..., examples=[2])


class SearchResponse(BaseModel):
    request_id: str
    tenant_id: str
    count: int
    faces: list[SearchFace]
    threshold: float = Field(..., examples=[0.4])
    inference_ms: float
    index: SearchIndexInfo


class HealthResponse(BaseModel):
    status: str = Field("ok", examples=["ok"])
    uptime_s: float = Field(..., examples=[128.4])


class ReadyResponse(BaseModel):
    ready: bool = Field(..., description="true = model + FAISS sẵn sàng nhận request.")
    detail: str = Field(..., examples=["ready", "loading product model", "sync embeddings"])
    modalities: dict = Field(
        ..., description="Trạng thái từng modality (face / product): enabled, provider, model, indexed.",
        examples=[{
            "face": {"enabled": True, "provider": "CUDAExecutionProvider", "model": "buffalo_l",
                     "indexed": {"t_demo": {"persons": 12, "vectors": 34}}},
            "product": {"enabled": True, "provider": "CUDAExecutionProvider", "model": "dinov2-small",
                        "indexed": {"t_demo": {"persons": 40, "vectors": 40}}},
        }],
    )


class GpuResponse(BaseModel):
    provider: Optional[str] = Field(None, examples=["CUDAExecutionProvider"])
    onnxruntime_providers: list[str] = Field(
        ..., examples=[["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]]
    )
    name: Optional[str] = Field(None, examples=["NVIDIA GeForce GTX 1650"])
    vram_total_mb: Optional[int] = Field(None, examples=[4096])
    vram_used_mb: Optional[int] = Field(None, examples=[686])
    gpu_util_pct: Optional[int] = Field(None, examples=[0])


class ReloadResponse(BaseModel):
    reloaded: dict = Field(
        ..., description="{modality: [tenant_id, ...]}",
        examples=[{"face": ["t_demo"], "product": ["t_demo"]}],
    )
    stats: dict = Field(
        ..., examples=[{"face": {"t_demo": {"persons": 2, "vectors": 2}},
                        "product": {"t_demo": {"persons": 40, "vectors": 40}}}],
    )


class StatsResponse(BaseModel):
    stats: dict = Field(..., examples=[{"t_demo": {"persons": 2, "vectors": 2}}])


class ErrorResponse(BaseModel):
    detail: str = Field(..., examples=["Token không hợp lệ"])


# ---- product (visual search, 1 ảnh = 1 sp) ----

class ProductEmbedResponse(BaseModel):
    request_id: str
    tenant_id: str
    model: str = Field(..., examples=["dinov2-small"])
    dim: int = Field(384, examples=[384])
    embedding: list[float] = Field(
        ..., description="Vector 384-d đã L2-norm. Gửi vào backend khi enroll sản phẩm.",
        min_length=384, max_length=384,
        examples=[[0.021, -0.044, 0.011, "...(381 số nữa)"]],
    )
    inference_ms: float


class ProductCandidate(BaseModel):
    product_id: str = Field(..., examples=["p_9f2c1a"])
    name: Optional[str] = Field(None, examples=["Coca 330ml lon"])
    score: float = Field(..., description="Cosine similarity (0..1).", examples=[0.87])


class ProductIndexInfo(BaseModel):
    products: int = Field(..., examples=[40])
    vectors: int = Field(..., examples=[40])


class ProductSearchResponse(BaseModel):
    request_id: str
    tenant_id: str
    match: Optional[ProductCandidate] = Field(
        None, description="Candidate top-1 nếu score >= threshold, ngược lại null."
    )
    candidates: list[ProductCandidate]
    threshold: float = Field(..., examples=[0.55])
    inference_ms: float
    index: ProductIndexInfo


class RootManifest(BaseModel):
    """Bản mô tả gọn cho AI agent / client tự khám phá."""

    service: str
    version: str
    ready_url: str
    modalities: dict
    docs: dict
    auth: dict
    endpoints: list[dict]
    enroll_flow: list[str]
    notes: list[str]


# ---- body (person ReID toàn thân) ----

class BodyEmbedResponse(BaseModel):
    request_id: str
    tenant_id: str
    model: str = Field(..., examples=["osnet_x1_0"])
    dim: int = Field(512, examples=[512])
    embedding: list[float] = Field(
        ..., description="Vector body ReID đã L2-norm. Enroll cùng person_id với face.",
        examples=[[0.031, -0.012, 0.044, "...(còn lại)"]],
    )
    inference_ms: float


class BodyCandidate(BaseModel):
    person_id: str = Field(..., examples=["P00125"])
    name: Optional[str] = Field(None, examples=["Nguyen Van A"])
    score: float = Field(..., description="Cosine similarity (0..1).", examples=[0.86])


class BodyIndexInfo(BaseModel):
    persons: int = Field(..., examples=[12])
    vectors: int = Field(..., examples=[40])


class BodySearchResponse(BaseModel):
    request_id: str
    tenant_id: str
    match: Optional[BodyCandidate] = Field(None, description="Top-1 nếu score >= threshold.")
    candidates: list[BodyCandidate]
    threshold: float = Field(..., examples=[0.5])
    inference_ms: float
    index: BodyIndexInfo


class ClothingAttributes(BaseModel):
    upper_color: str = Field(..., examples=["red"])
    upper_rgb: list[int] = Field(..., examples=[[190, 40, 35]])
    lower_color: str = Field(..., examples=["black"])
    lower_rgb: list[int] = Field(..., examples=[[20, 20, 22]])


class PersonMatch(BaseModel):
    person_id: str = Field(..., examples=["P00125"])
    name: Optional[str] = Field(None, examples=["Nguyen Van A"])
    confidence: float = Field(..., description="Điểm fusion cuối (0..1).", examples=[0.91])
    face_score: Optional[float] = Field(None, examples=[0.95])
    body_score: Optional[float] = Field(None, examples=[0.86])
    clothing_score: Optional[float] = Field(
        None, description="Phase 1: null (cần backend lưu attributes gallery).", examples=[None]
    )
    sources: list[str] = Field(..., description="Tín hiệu đã đóng góp.", examples=[["face", "body"]])


class PersonIdentifyResponse(BaseModel):
    request_id: str
    tenant_id: str
    match: Optional[PersonMatch] = Field(None, description="Top-1 nếu đạt ngưỡng face HOẶC body.")
    candidates: list[PersonMatch]
    attributes: Optional[ClothingAttributes] = None
    face_visible: bool
    inference_ms: float
