#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""上传封面 = 成片前 0.5s 内的一帧（用户 2026-11-20 口径）。

为什么：封面已经拼在成片最前面 0.5s（`executors._prepend_cover`），
所以**前 0.5 秒每一帧都是封面帧**。需要单独上传封面的平台（B 站必须有封面），
就应该从这 0.5s 里抽一帧，而不是另画一张 —— 这样平台侧封面 == 平台自抽首帧，
不会再出现「上传的封面和视频首帧不一致」的回读失败。

三条硬规则：
  1. 抽帧点必须在 [0, 0.5)s 内，默认 0.25s（避开 h264 首帧关键帧的边缘）；
  2. 抽完要自证：0.00 / 0.25 / 0.45 三帧必须几乎一致（否则说明这段不是静止封面片头，
     例如横版没贴片头 → fail-closed，绝不把正片画面当封面传上去）；
  3. 抽帧结果与 `cover.png` 的 dHash 距离要小（同一张图，容许编码差异）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

COVER_LEAD_SECONDS = 0.5          # 与 executors._prepend_cover 保持同一口径
UPLOAD_FRAME_AT = 0.25            # 默认抽帧点（落在 0..0.5s 中间）
PROBE_POINTS = (0.00, 0.25, 0.45)  # 静止性自证点
DHASH_MAX_STATIC = 8              # 三帧之间允许的最大汉明距离（同一张图）
DHASH_MAX_VS_COVER = 16           # 与 cover.png 允许的最大距离（h264 编码差异）


class CoverLeadError(RuntimeError):
    """前 0.5s 不是静止封面帧，不能拿去当上传封面。"""


def _grab_gray(video: Path, at_sec: float, size: int = 32) -> list[int]:
    # 精确 seek：-ss 放在 -i 之后（放前面是按关键帧快跳，h264 上会读到半帧数据，
    # 实测报 `Invalid NAL unit size`）；0s 直接用首帧。
    seek = ["-ss", f"{at_sec:.3f}"] if at_sec > 0 else []
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video), *seek,
         "-frames:v", "1", "-an", "-vf", f"scale={size}:{size},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout:
        raise CoverLeadError(f"抽帧失败 {video.name}@{at_sec}s: {p.stderr.decode()[:120]}")
    return list(p.stdout)


def _dhash(gray: list[int], size: int = 32) -> int:
    """32x32 灰度 → 横向差分 64bit 指纹。"""
    rows = 16
    cols = 16
    step = size // rows
    bits = 0
    idx = 0
    for r in range(rows):
        for c in range(cols):
            left = gray[r * size * step + c * step * 2]
            right = gray[r * size * step + c * step * 2 + step]
            if left > right:
                bits |= 1 << idx
            idx += 1
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def lead_in_dhash(video: Path | str, at_sec: float = UPLOAD_FRAME_AT) -> int:
    return _dhash(_grab_gray(Path(video), at_sec))


def make_upload_cover(video: Path | str, out_png: Path | str, *,
                      at_sec: float = UPLOAD_FRAME_AT,
                      cover_ref: Path | str | None = None,
                      strict: bool = True) -> dict[str, object]:
    """从成片前 0.5s 抽一帧存成上传用封面，并自证这段确实是静止封面。

    strict=True（默认）：三帧不一致或与 cover_ref 差太多 → 抛 CoverLeadError。
    """
    video, out_png = Path(video), Path(out_png)
    if not video.exists():
        raise CoverLeadError(f"成片不存在: {video}")
    if not (0.0 <= at_sec < COVER_LEAD_SECONDS):
        raise CoverLeadError(f"抽帧点 {at_sec}s 必须落在 [0, {COVER_LEAD_SECONDS}) 内")

    hashes = {t: _dhash(_grab_gray(video, t)) for t in PROBE_POINTS}
    base = hashes[PROBE_POINTS[0]]
    spread = max(_hamming(base, h) for h in hashes.values())

    ref = Path(cover_ref) if cover_ref else None
    vs_cover = None
    if ref and ref.exists():
        vs_cover = _hamming(hashes[at_sec], lead_in_dhash(ref, 0.0) if ref.suffix == ".png" else 0)

    if strict and spread > DHASH_MAX_STATIC:
        raise CoverLeadError(
            f"前 0.5s 不是静止封面帧（三帧 dHash 最大差 {spread} > {DHASH_MAX_STATIC}）："
            f"{video.name} 很可能没贴封面片头，拒绝把正片画面当封面上传")
    if strict and vs_cover is not None and vs_cover > DHASH_MAX_VS_COVER:
        raise CoverLeadError(
            f"抽帧与封面图差太多（dHash {vs_cover} > {DHASH_MAX_VS_COVER}）：{video.name}")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    seek = ["-ss", f"{at_sec:.3f}"] if at_sec > 0 else []
    p = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", str(video), *seek,
         "-frames:v", "1", "-an", "-q:v", "2", str(out_png)],
        capture_output=True, timeout=60)
    if p.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
        raise CoverLeadError(f"封面抽帧写文件失败: {p.stderr.decode()[:120]}")

    return {
        "path": str(out_png),
        "at_sec": at_sec,
        "window_sec": COVER_LEAD_SECONDS,
        "static_spread": spread,
        "dhash_vs_cover": vs_cover,
        "source_video": str(video),
    }


def main(argv: list[str]) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="从成片前 0.5s 抽一帧当上传封面")
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--at", type=float, default=UPLOAD_FRAME_AT)
    ap.add_argument("--cover-ref", default=None)
    ap.add_argument("--no-strict", action="store_true")
    a = ap.parse_args(argv)
    info = make_upload_cover(a.video, a.out, at_sec=a.at,
                             cover_ref=a.cover_ref, strict=not a.no_strict)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
