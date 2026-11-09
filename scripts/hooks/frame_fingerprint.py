#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.frame_fingerprint（transform）—— 画面指纹。

取视频 0.2s 处的灰度帧，算 sha256 作为画面指纹（用于跨任务视觉查重：
同一张封面/同一段开场不应反复出现）。ffmpeg 不可用或帧取不到 → ok=false。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    key = str(c.get("video_key") or "video")
    video = C.artifact(p, key) or C.artifact(p, "video_16_9")
    if video is None or not video.exists():
        C.fail(f"frame_fingerprint: 找不到视频产物（artifacts.{key}）")
    if video.stat().st_size < 1024:
        C.fail(f"frame_fingerprint: 视频文件过小（{video.stat().st_size} bytes），疑似空文件")

    got = C.frame_gray(video, ts=0.2)
    if not got:
        C.fail("frame_fingerprint: 抽取视频帧失败（ffmpeg/ffprobe 异常或文件不可解码）")
    data, w, h = got
    if not data:
        C.fail("frame_fingerprint: 抽帧返回空数据")

    import hashlib
    fp = hashlib.sha256(data).hexdigest()
    C.emit(True, patch={
        "frame_fp": fp[:16],
        "frame_fp_full": fp,
        "frame_size": f"{w}x{h}",
        "frame_bytes": len(data),
    })


if __name__ == "__main__":
    main()
