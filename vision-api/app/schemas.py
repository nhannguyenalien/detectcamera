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
    detail: str = Field(..., examples=["ready", "loading model", "sync embeddings"])
    provider: Optional[str] = Field(None, examples=["CUDAExecutionProvider"])
    model: str = Field(..., examples=["buffalo_l"])
    indexed: dict = Field(
        ..., description="Thống kê index theo tenant.",
        examples=[{"t_demo": {"persons": 12, "vectors": 34}}],
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
    reloaded: list[str] = Field(..., examples=[["t_demo"]])
    stats: dict = Field(..., examples=[{"t_demo": {"persons": 2, "vectors": 2}}])


class StatsResponse(BaseModel):
    stats: dict = Field(..., examples=[{"t_demo": {"persons": 2, "vectors": 2}}])


class ErrorResponse(BaseModel):
    detail: str = Field(..., examples=["Token không hợp lệ"])


class RootManifest(BaseModel):
    """Bản mô tả gọn cho AI agent / client tự khám phá."""

    service: str
    version: str
    ready_url: str
    docs: dict
    auth: dict
    endpoints: list[dict]
    enroll_flow: list[str]
    notes: list[str]
