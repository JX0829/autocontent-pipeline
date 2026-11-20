"""首帧即封面：本地自证（2026-11-20 用户口径）。

背景：三个平台统一「封面拼在视频最前面 0.5s」的方案（executors 里 `_prepend_cover`，
竖版 2026-11-18 §十二、横版 6bb6a2d 已落地）。视频首帧 == cover.png。
所以 provider 侧**不再上传独立封面**，也不再做抖音 preview dHash 回读
（那套判据在 211×376 缩略图上不成立：实测同一张封面 dist 17~33 都能出，
真正的错因是弹窗未关导致截图区域失效）。

这里只做一件本地事：确认视频真的以封面开头，否则 fail-closed。
纯 PIL 实现 SSIM，不引入 numpy/scipy。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

MIN_SSIM = 0.85


def first_frame(video: str | Path, out: str | Path | None = None) -> str:
    """抽第 0 帧（不解码全片）。"""
    dst = str(out or (Path(tempfile.mkdtemp()) / "first_frame.png"))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", dst],
        check=True, capture_output=True,
    )
    return dst


def ssim_pair(pa: str | Path, pb: str | Path, size: int = 64, win: int = 8) -> float:
    """两张图的结构相似度（0~1）。同图 0.99+，不同图 ~0.01~0.5。"""
    from PIL import Image

    a_img = Image.open(pa).convert("L").resize((size, size), Image.LANCZOS)
    b_img = Image.open(pb).convert("L").resize((size, size), Image.LANCZOS)
    a = list(a_img.getdata())
    b = list(b_img.getdata())
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    total, count = 0.0, 0
    for y0 in range(0, size - win + 1, win):
        for x0 in range(0, size - win + 1, win):
            sx = sy = sxx = syy = sxy = 0.0
            n = 0
            for yy in range(y0, y0 + win):
                base = yy * size
                for xx in range(x0, x0 + win):
                    q = base + xx
                    vx, vy = a[q], b[q]
                    sx += vx
                    sy += vy
                    sxx += vx * vx
                    syy += vy * vy
                    sxy += vx * vy
                    n += 1
            mx, my = sx / n, sy / n
            vx_, vy_ = sxx / n - mx * mx, syy / n - my * my
            cxy = sxy / n - mx * my
            total += ((2 * mx * my + c1) * (2 * cxy + c2)) / (
                (mx * mx + my * my + c1) * (vx_ + vy_ + c2))
            count += 1
    return total / max(1, count)


def check_video_starts_with_cover(video: str | Path, cover: str | Path,
                                  min_ssim: float = MIN_SSIM) -> dict:
    """自证：视频首帧 ≈ cover.png。返回 {ok, code, detail, ssim}。"""
    video, cover = Path(video), Path(cover)
    if not video.exists():
        return {"ok": False, "code": "VIDEO_MISSING", "ssim": None,
                "detail": f"视频不存在：{video}"}
    if not cover.exists():
        return {"ok": False, "code": "COVER_MISSING", "ssim": None,
                "detail": f"封面不存在：{cover}"}
    try:
        frame = first_frame(video)
        s = ssim_pair(frame, cover)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "COVER_CHECK_ERROR", "ssim": None,
                "detail": f"{type(exc).__name__}: {exc}"}
    ok = s >= min_ssim
    return {"ok": ok, "code": "" if ok else "COVER_LEAD_IN_MISSING", "ssim": round(s, 4),
            "detail": f"首帧 vs cover SSIM={s:.4f}（阈值 {min_ssim}）"}
