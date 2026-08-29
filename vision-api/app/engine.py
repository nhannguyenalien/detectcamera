"""Bọc InsightFace: SCRFD (detection) + ArcFace (recognition) trên CUDA."""
import io
import time

import numpy as np
from PIL import Image

from . import config

# Chống decompression bomb: ảnh quá nhiều pixel -> PIL raise DecompressionBombError
Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS


class FaceEngine:
    def __init__(self) -> None:
        self.app = None
        self.provider: str | None = None

    def load(self) -> None:
        import onnxruntime as ort
        from insightface.app import FaceAnalysis

        avail = ort.get_available_providers()
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
        if not providers:
            providers = ["CPUExecutionProvider"]

        # chỉ nạp detection + recognition -> tiết kiệm VRAM (bỏ landmark/genderage)
        self.app = FaceAnalysis(
            name=config.INSIGHTFACE_MODEL,
            root=config.MODEL_ROOT,
            allowed_modules=["detection", "recognition"],
            providers=providers,
        )
        ctx_id = 0 if "CUDAExecutionProvider" in providers else -1
        self.app.prepare(
            ctx_id=ctx_id,
            det_thresh=config.DET_THRESH,
            det_size=(config.DET_SIZE, config.DET_SIZE),
        )
        self.provider = providers[0]

    def warmup(self) -> None:
        blank = np.zeros((config.DET_SIZE, config.DET_SIZE, 3), dtype=np.uint8)
        self.app.get(blank)

    @staticmethod
    def decode(raw: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        # InsightFace kỳ vọng BGR (OpenCV convention)
        return np.ascontiguousarray(np.array(img)[:, :, ::-1])

    def analyze(self, bgr: np.ndarray) -> tuple[list[dict], float]:
        t0 = time.perf_counter()
        faces = self.app.get(bgr)
        out: list[dict] = []
        for f in faces:
            x1, y1, x2, y2 = (round(float(v), 1) for v in f.bbox.tolist())
            out.append(
                {
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "det_score": round(float(f.det_score), 4),
                    "embedding": np.asarray(f.normed_embedding, dtype=np.float32),
                }
            )
        return out, round((time.perf_counter() - t0) * 1000, 1)
