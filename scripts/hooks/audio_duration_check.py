#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.audio_duration_check（gate, warn）—— 音画时长偏差。

期望时长 = narration 字数 / 4.5（Edge-TTS +10% 经验值）。
实测用 ffprobe 取容器时长；偏差 > 30% → warn（多半是 TTS 漏句/静音段/音轨缺失）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

TOLERANCE = 0.30
CHARS_PER_SEC = 4.5


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    video = C.artifact(p, str(c.get("video_key") or "video")) or C.artifact(p, "video_16_9")
    if video is None or not video.exists():
        C.fail("audio_duration_check: 找不到视频产物")

    actual = C.video_duration(video)
    if actual is None or actual <= 0:
        C.fail("audio_duration_check: ffprobe 读不出时长（音轨/容器异常）")

    narration = c.get("narration") or []
    chars = sum(len(str(s)) for s in narration) if isinstance(narration, list) else len(str(narration))
    if not chars:
        C.fail("audio_duration_check: 缺 narration，无法推算期望时长（上游 hook.expand_narration 未跑？）")

    expected = chars / CHARS_PER_SEC
    drift = abs(actual - expected) / expected if expected else 1.0

    info = {"actual_sec": round(actual, 2), "expected_sec": round(expected, 1),
            "drift": round(drift, 3), "narration_chars": chars}
    if drift > TOLERANCE:
        C.fail(f"audio_duration_check: 时长偏差 {drift:.0%}（实际 {actual:.1f}s vs 期望 {expected:.1f}s）", **info)

    C.emit(True, patch=info)


if __name__ == "__main__":
    main()
