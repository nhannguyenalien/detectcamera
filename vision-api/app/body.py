"""Body / Person ReID — ONNX CNN embedder cho crop TOÀN THÂN người.

Tương thích OSNet (torchreid), OMZ person-reidentification-retail, hoặc CNN
embedder bất kỳ: tự đọc tên + shape input từ session, tiền xử lý ImageNet
chuẩn (resize H×W, normalize), output được L2-normalize để so cosine.

KHÔNG dùng 1 model duy nhất để định danh — đây chỉ là 1 tín hiệu, được fusion
với face (+ clothing) ở /v1/persons/identify. Quần áo mạnh nhưng không bền:
khi người đổi đồ, body-shape/đầu-vai còn giúp phần nào, gait (phase sau) tốt hơn.
"""
import io
import time

import numpy as np
from PIL import Image

from . import config

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_BILINEAR = getattr(Image, "Resampling", Image).BILINEAR


class BodyEngine:
    def __init__(self) -> None:
        self.sess = None
        self.provider: str | None = None
        self._in_name: str | None = None
        self._h = config.BODY_INPUT_H
        self._w = config.BODY_INPUT_W
        self.dim = config.BODY_EMB_DIM

    def load(self) -> None:
        import onnxruntime as ort

        avail = ort.get_available_providers()
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in avail]
        if not providers:
            providers = ["CPUExecutionProvider"]
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(config.BODY_MODEL_PATH, sess_options=so, providers=providers)
        self.provider = self.sess.get_providers()[0]
        inp = self.sess.get_inputs()[0]
        self._in_name = inp.name
        shp = inp.shape  # kỳ vọng [N, 3, H, W]
        if len(shp) == 4 and isinstance(shp[2], int) and isinstance(shp[3], int):
            self._h, self._w = int(shp[2]), int(shp[3])
        out_shape = self.sess.get_outputs()[0].shape
        if len(out_shape) >= 2 and isinstance(out_shape[-1], int):
            self.dim = int(out_shape[-1])

    def warmup(self) -> None:
        x = (np.random.rand(1, 3, self._h, self._w).astype(np.float32) * 255.0)
        self.sess.run(None, {self._in_name: x})

    def _preprocess(self, raw: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(raw)).convert("RGB").resize((self._w, self._h), _BILINEAR)
        arr = np.asarray(img, dtype=np.float32)
        if config.BODY_PREPROCESS == "raw_bgr":
            # OMZ person-reidentification-retail: BGR, 0..255, KHÔNG mean/std
            arr = np.ascontiguousarray(arr[:, :, ::-1])
        else:
            # ImageNet: RGB, /255, chuẩn hoá theo mean/std (OSNet torchreid, DINOv2-style)
            arr = (arr / 255.0 - _MEAN) / _STD
        return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # [1,3,H,W]

    def embed(self, raw: bytes) -> tuple[np.ndarray, float]:
        t0 = time.perf_counter()
        x = self._preprocess(raw)
        outs = self.sess.run(None, {self._in_name: x})
        vec = np.asarray(outs[0], dtype=np.float32).reshape(-1)
        n = np.linalg.norm(vec)
        if n > 0:
            vec = vec / n
        return vec, round((time.perf_counter() - t0) * 1000, 1)
