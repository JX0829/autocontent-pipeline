"""图片指纹（dHash）—— 用来证明「后台封面真的等于我上传的那张」。

为什么需要：封面这类「选了就算」的操作用肉眼 unavoidable 不可自动化验收。平台回读能拿到
的往往只是 `blob:` / `data:` 的预览图，没法比文件名；能比的只有**像素**。
dHash 对缩放、轻微压缩鲁棒，对「完全不同的图」极敏感（汉明距离通常 >25）。

实测口径（2026-11-18）：cover.png 与它自己的 320px 缩略图距离 **0~2**；
与另一张完全不同底图的封面距离 **>20**。所以阈值取 12。
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

DEFAULT_THRESHOLD = 12


def dhash(image: Union[str, Path, bytes], size: int = 8) -> int:
    """返回 64bit dHash（差分成 0/1 串拼成的整数）。"""
    import io

    from PIL import Image

    raw = image if isinstance(image, bytes) else Path(image).read_bytes()
    im = Image.open(io.BytesIO(raw)).convert("L").resize((size + 1, size))
    px = list(im.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def same_image(a: Union[str, Path, bytes], b: Union[str, Path, bytes],
               threshold: int = DEFAULT_THRESHOLD) -> tuple[bool, int]:
    """(是否同一张图, 汉明距离)。threshold 越小越严格。"""
    dist = hamming(dhash(a), dhash(b))
    return dist <= threshold, dist


#: 平台会把封面重新裁切（抖音竖封面是 3:4，不是 9:16），所以「同一张图」要按候选裁切比。
CROP_RATIOS = (None, 3 / 4, 9 / 16, 1.0, 4 / 3)


def _center_crop(raw: bytes, ratio: float) -> bytes:
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(raw))
    w, h = im.size
    cur = w / h
    if abs(cur - ratio) < 0.01:
        return raw
    if cur > ratio:                       # 太宽 → 裁左右
        nw = int(h * ratio)
        box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
    else:                                 # 太高 → 裁上下
        nh = int(w / ratio)
        box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
    buf = io.BytesIO()
    im.crop(box).save(buf, "PNG")
    return buf.getvalue()


def same_image_any_crop(source: Union[str, Path, bytes],
                        other: Union[str, Path, bytes],
                        threshold: int = DEFAULT_THRESHOLD) -> tuple[bool, int, str]:
    """按若干裁切比逐个比，取最小距离：平台裁切过的封面也认得出是同一张。

    返回 (是否同一张, 最小汉明距离, 命中的裁切比说明)。
    """
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    best_dist, best_label = 10 ** 6, "none"
    for ratio in CROP_RATIOS:
        cand = raw if ratio is None else _center_crop(raw, ratio)
        dist = hamming(dhash(cand), dhash(other))
        label = "原图" if ratio is None else f"{ratio:.2f}"
        if dist < best_dist:
            best_dist, best_label = dist, label
    return best_dist <= threshold, best_dist, best_label
