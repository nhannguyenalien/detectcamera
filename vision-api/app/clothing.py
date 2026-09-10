"""Thuộc tính trang phục — dùng để LỌC NHANH, KHÔNG phải ID cố định.

Lấy màu chủ đạo vùng thân trên (áo) và thân dưới (quần) từ crop toàn thân.
Thuần numpy/PIL — không model, không tốn VRAM. Khi có pose (phase 2) thì thay
vùng cắt cứng bằng vùng theo keypoint vai/hông cho chuẩn hơn.
"""
import io

import numpy as np
from PIL import Image

# tên màu + RGB đại diện (đủ cho filter "áo đỏ / quần đen / áo trắng")
_NAMED = [
    ("black", (15, 15, 15)),
    ("white", (245, 245, 245)),
    ("gray", (128, 128, 128)),
    ("red", (200, 30, 30)),
    ("orange", (230, 130, 30)),
    ("yellow", (235, 220, 50)),
    ("green", (40, 160, 60)),
    ("blue", (40, 80, 190)),
    ("navy", (20, 30, 90)),
    ("purple", (120, 50, 160)),
    ("pink", (230, 130, 180)),
    ("brown", (110, 70, 40)),
    ("beige", (215, 195, 160)),
]


def _name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return min(_NAMED, key=lambda c: (c[1][0] - r) ** 2 + (c[1][1] - g) ** 2 + (c[1][2] - b) ** 2)[0]


def _dominant(a: np.ndarray) -> tuple[int, int, int]:
    """Màu trung vị, bỏ pixel quá tối/sáng (bóng, nền, cháy sáng)."""
    if a.size == 0:
        return (0, 0, 0)
    flat = a.reshape(-1, 3).astype(np.float32)
    lum = flat.mean(axis=1)
    keep = flat[(lum > 15) & (lum < 245)]
    if len(keep) < 20:
        keep = flat
    med = np.median(keep, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def attributes(raw: bytes) -> dict:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    a = np.asarray(img)
    upper = a[int(h * 0.15):int(h * 0.50), int(w * 0.20):int(w * 0.80)]
    lower = a[int(h * 0.55):int(h * 0.90), int(w * 0.25):int(w * 0.75)]
    up = _dominant(upper)
    lo = _dominant(lower)
    return {
        "upper_color": _name(up),
        "upper_rgb": [up[0], up[1], up[2]],
        "lower_color": _name(lo),
        "lower_rgb": [lo[0], lo[1], lo[2]],
    }


def similarity(x: dict | None, y: dict | None) -> float | None:
    """0..1 giữa 2 bộ attributes (nửa trên + nửa dưới, theo khoảng cách RGB)."""
    if not x or not y or "upper_rgb" not in x or "upper_rgb" not in y:
        return None

    def d(p, q):
        e = sum((pi - qi) ** 2 for pi, qi in zip(p, q)) ** 0.5
        return max(0.0, 1.0 - e / 441.67)  # 441.67 = sqrt(3*255^2)

    return round(0.5 * d(x["upper_rgb"], y["upper_rgb"]) + 0.5 * d(x["lower_rgb"], y["lower_rgb"]), 4)
