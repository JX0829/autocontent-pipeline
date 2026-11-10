#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.frame_sample_check（gate, block）—— 成品画面抽样质检。

对应 QA **M5**：出厂质检只看分辨率，横屏片通过 ffprobe 但第 0.5 秒封面是竖版裁切图。

三条判据：
  1. 0.5s 与 2s 两帧都能解码（片子不是坏文件）
  2. 两帧灰度标准差 > 3.0（不是纯色/黑场/白屏）
  3. 宽高比符合 context.aspect 期望（默认按帧自身比例记，声明 aspect 时强校验）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

MIN_STDDEV = 3.0
ASPECT_TOLERANCE = 0.03


def _stddev(data: bytes) -> float:
    if not data:
        return 0.0
    n = len(data)
    mean = sum(data) / n
    var = sum((b - mean) ** 2 for b in data) / n
    return var ** 0.5


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    key = str(c.get("video_key") or "video")
    video = C.artifact(p, key) or C.artifact(p, "video_16_9")
    if video is None or not video.exists():
        C.fail(f"frame_sample_check: 找不到视频产物（artifacts.{key}）")

    size = C.video_size(video)
    if not size:
        C.fail("frame_sample_check: ffprobe 读不出分辨率（文件损坏或非视频）")
    w, h = size

    samples = {}
    for ts in (0.5, 2.0):
        got = C.frame_gray(video, ts=ts)
        if not got:
            C.fail(f"frame_sample_check: {ts}s 处抽帧失败（画面损坏/时长不足）")
        data, fw, fh = got
        sd = _stddev(data)
        samples[f"{ts}s"] = round(sd, 2)
        if sd < MIN_STDDEV:
            C.fail(f"frame_sample_check: {ts}s 画面近似纯色（stddev={sd:.2f} < {MIN_STDDEV}），疑似错版/黑场",
                   samples=samples)

    expected = str(c.get("aspect") or "")
    want = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0}.get(expected)
    ratio = w / h if h else 0
    if want and abs(ratio - want) / want > ASPECT_TOLERANCE:
        C.fail(f"frame_sample_check: 宽高比 {w}x{h}={ratio:.3f} 不符声明 {expected}", samples=samples)

    C.emit(True, patch={"frame_samples": samples, "frame_size": f"{w}x{h}", "aspect": expected or "auto"})


if __name__ == "__main__":
    main()
