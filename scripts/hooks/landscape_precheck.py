#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.landscape_precheck（gate, block）—— 横屏渲染前置检查。

在调用 Remotion 之前拦住注定错版的输入（QA **M5** 的上游成因）：
  1. 必须声明画幅 `context.aspect == "16:9"`（避免拿竖版产物冒充横版）
  2. 必须提供横版封面源（`artifacts.cover_source`）或显式 `context.landscape_cover="generate"`
  3. 若提供封面源，必须是横向图（宽 > 高），且分辨率 >= 1280x720
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

MIN_W, MIN_H = 1280, 720


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    aspect = str(c.get("aspect") or "")
    if aspect != "16:9":
        C.fail(f"landscape_precheck: 画幅声明为 {aspect or '(空)'}，横屏步骤要求 16:9")

    cover_src = C.artifact(p, "cover_source")
    mode = str(c.get("landscape_cover") or "")
    if cover_src is None and mode != "generate":
        C.fail("landscape_precheck: 既无 cover_source 也未声明 landscape_cover=generate（会退化成竖版裁切）")
    if cover_src is not None:
        if not cover_src.exists():
            C.fail(f"landscape_precheck: cover_source 不存在 {cover_src}")
        size = C.video_size(cover_src)  # 图片同样可被 ffprobe 读
        if not size:
            C.fail("landscape_precheck: cover_source 无法读取尺寸（非图片/损坏）")
        w, h = size
        if w <= h:
            C.fail(f"landscape_precheck: cover_source 是竖版 {w}x{h}，塞进 16:9 会留大片空白")
        if w < MIN_W or h < MIN_H:
            C.fail(f"landscape_precheck: cover_source 分辨率不足 {w}x{h}（要求 >={MIN_W}x{MIN_H}）")

    C.emit(True, patch={"landscape_precheck": "ok", "cover_mode": mode or "source", "aspect": aspect})


if __name__ == "__main__":
    main()
