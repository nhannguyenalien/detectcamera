"""Product visual search — DINOv2-S (ONNX) → embedding 384-d cho toàn ảnh (1 ảnh = 1 sp)."""
import io
import time

import numpy as np
from PIL import Image

from . import config

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_BICUBIC = getattr(Image, "Resampling", Image).BICUBIC


class ProductEngine:
    def __init__(self) -> None:
        self.sess = None
        self.provider: str | None = None
        self._in_name: str | None = None
        self._pooler_idx: int | None = None  # index của 'pooler_output' nếu có

    def load(self) -> None:
        import onnxruntime as ort

        avail = ort.get_available_providers()
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
        if not providers:
            providers = ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(config.PRODUCT_MODEL_PATH, sess_options=so, providers=providers)
        self.provider = self.sess.get_providers()[0]
        self._in_name = self.sess.get_inputs()[0].name
        out_names = [o.name for o in self.sess.get_outputs()]
        self._pooler_idx = out_names.index("pooler_output") if "pooler_output" in out_names else None

    def warmup(self) -> None:
        s = config.PRODUCT_INPUT_SIZE
        self.sess.run(None, {self._in_name: np.zeros((1, 3, s, s), dtype=np.float32)})

    def _preprocess(self, raw: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        s = config.PRODUCT_INPUT_SIZE
        # resize cạnh ngắn -> 256/224*s, center-crop s (chuẩn eval DINOv2)
        short = round(s * 256 / 224)
        w, h = img.size
        scale = short / min(w, h)
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), _BICUBIC)
        w, h = img.size
        left, top = (w - s) // 2, (h - s) // 2
        img = img.crop((left, top, left + s, top + s))
        arr = (np.asarray(img, dtype=np.float32) / 255.0 - _MEAN) / _STD
        return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # [1,3,s,s]

    def embed(self, raw: bytes) -> tuple[np.ndarray, float]:
        t0 = time.perf_counter()
        x = self._preprocess(raw)
        outs = self.sess.run(None, {self._in_name: x})
        if self._pooler_idx is not None:
            vec = np.asarray(outs[self._pooler_idx][0], dtype=np.float32)
        else:  # CLS token của last_hidden_state [1, N, 384]
            vec = np.asarray(outs[0][0, 0], dtype=np.float32)
        n = np.linalg.norm(vec)
        if n > 0:
            vec = vec / n
        return vec, round((time.perf_counter() - t0) * 1000, 1)
