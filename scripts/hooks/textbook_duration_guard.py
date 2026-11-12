#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.textbook_duration_guard（gate, block）—— 课文故事出片时长断言。

为什么要有这一步（spec F-2026-11-12 §11.3 缺口 1）：
    目标时长写进物料（storyboard_hint.target_duration_sec=90）后，旧链路实测只出了
    29.13 秒 —— 模型和 TTS 都不会替你保证时长。所以**出片之后**必须硬断言，
    低于目标窗口就直接失败重做，而不是把 30 秒的片子当 90 秒故事片发出去。

判定口径（两个条件同时满足）：
    · 相对窗口：低于目标时长 70%（TEXTBOOK_TOLERANCE=0.30）直接判失败 —— 2026-11-12 用户口述判据
    · 绝对窗口：[TEXTBOOK_MIN_SEC, TEXTBOOK_MAX_SEC]（spec §8.2：90~150 秒）
    最终窗口 = 两者取交集：下限 = max(90, target×0.70)，上限 = min(150, target×1.30)
    内部 30 秒样片要临时放宽下限：TEXTBOOK_MIN_SEC=0

非课文故事物料（content_type 缺省 / tech_news）直接放行 —— 科技链路行为不变。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))
from src.pipeline.manifest import content_type_of, target_duration_sec  # noqa: E402

TEXTBOOK_CONTENT_TYPE = "textbook_story"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def duration_window(target_sec: int,
                    tolerance: float | None = None,
                    floor_sec: float | None = None,
                    ceil_sec: float | None = None) -> tuple[float, float]:
    """返回 (最低可接受秒数, 最高可接受秒数)。纯函数，供回归用例直接断言。"""
    tol = _env_float("TEXTBOOK_DURATION_TOLERANCE", 0.30) if tolerance is None else tolerance
    floor = _env_float("TEXTBOOK_MIN_SEC", 90.0) if floor_sec is None else floor_sec
    ceil = _env_float("TEXTBOOK_MAX_SEC", 150.0) if ceil_sec is None else ceil_sec
    target = max(1, int(target_sec))
    lo = max(floor, target * (1 - tol))
    hi = min(ceil, target * (1 + tol))
    return round(lo, 2), round(hi, 2)


def _manifest(payload: dict) -> dict | None:
    man = C.ctx(payload).get("manifest")
    if isinstance(man, dict):
        return man
    loaded = C.read_json(C.artifact(payload, "manifest"))
    return loaded if isinstance(loaded, dict) else None


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)

    man = _manifest(p)
    if isinstance(man, dict) and content_type_of(man) != TEXTBOOK_CONTENT_TYPE:
        C.emit(True, reason=f"skipped: content_type={content_type_of(man)}，非课文故事不做时长断言")
    if not isinstance(man, dict):
        # 拿不到物料就无法判定目标时长：课文故事属于硬失败，科技链路（无物料干跑）放行
        C.fail("textbook_duration_guard: 缺 manifest（context.manifest / artifacts.manifest）")

    target = target_duration_sec(man)
    lo, hi = duration_window(target)

    video = None
    for key in (str(c.get("video_key") or "video"), "video", "video_16_9"):
        video = C.artifact(p, key)
        if video is not None and video.exists():
            break
    if video is None or not video.exists():
        C.fail("textbook_duration_guard: 找不到成片（artifacts.video / video_16_9）")

    actual = C.video_duration(video)
    if actual is None or actual <= 0:
        C.fail(f"textbook_duration_guard: ffprobe 读不出时长（{video.name}）")

    info = {"target_sec": target, "actual_sec": round(actual, 2),
            "window_sec": [lo, hi], "file": video.name}

    if actual < lo:
        C.fail(
            f"课文故事成片过短：{actual:.1f}s < 下限 {lo:.0f}s"
            f"（目标 {target}s，spec 要求 90~150s；旁白字数/镜数不足，需重做分镜）", **info)
    if actual > hi:
        C.fail(
            f"课文故事成片过长：{actual:.1f}s > 上限 {hi:.0f}s"
            f"（目标 {target}s，spec 要求 90~150s；旁白需压缩）", **info)

    C.emit(True, patch=info, reason=f"时长断言通过：{actual:.1f}s ∈ [{lo:.0f}, {hi:.0f}]s")


if __name__ == "__main__":
    main()
