#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPENING_LUMA 契约（BUGS-20261120 R1/R2/R4/R5）离线回归。

判据（竖版、横版各自成立）：
  1. 首帧近黑像素比 ≤ 40%
  2. 片头 0–0.5s 内不得出现 ≥0.2s 的暗段（YAVG ≤ 26）

还覆盖两条「不许再犯」：
  · 暗底导致的横版覆盖率不足，报错不得写成「竖版封面塞横屏」（R4）
  · 绑定阶段 bg 首帧太暗要换素材；整池都暗必须**出片前**报 OPENING_TOO_DARK（R2b）

跑法：python3 scripts/test_opening_luma.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.utils.black_check import check_opening  # noqa: E402

PASS = FAIL = 0
FFMPEG = "/opt/homebrew/bin/ffmpeg"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def make_video(path: Path, color: str = "0x6e7f90", dur: str = "1.2") -> Path:
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi",
                    "-i", f"color=c={color}:s=360x640:d={dur}:r=30",
                    "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="openluma_"))
    bright = make_video(tmp / "bright.mp4", "0x6e7f90")
    dark = make_video(tmp / "dark.mp4", "0x08090b")
    gray = make_video(tmp / "gray.mp4", "0x3a3f46")

    print("── 片头亮度判据 ──")
    b = check_opening(bright)
    check("亮片头 PASS", b["ok"], str(b))
    check("亮片头首帧近黑比 ≤40%", b["first_frame_ratio"] <= 0.40, str(b["first_frame_ratio"]))

    d = check_opening(dark)
    check("暗片头 FAIL 且 code=OPENING_TOO_DARK", (not d["ok"]) and d["code"] == "OPENING_TOO_DARK", str(d))
    check("暗片头首帧近黑比 >40%", d["first_frame_ratio"] > 0.40, str(d["first_frame_ratio"]))
    check("失败原因里带亮度指标", "近黑" in d["reason"] and "YAVG" in d["reason"], d["reason"])

    g = check_opening(gray)
    check("中灰（≈Y58）PASS", g["ok"], str(g))

    print("── 横版守卫归因（R4）──")
    sys.path.insert(0, str(PROJ / "scripts"))
    import landscape_guard as LG  # noqa: E402

    info = LG._opening_luma(str(dark))
    check("暗片头被标记 opening_dark", info.get("opening_dark") is True, str(info))
    check("归因指标带首帧近黑比", float(info.get("first_frame_ratio") or 0) > 0.4, str(info))
    info_b = LG._opening_luma(str(bright))
    check("亮片头不会被标记 opening_dark", info_b.get("opening_dark") is False, str(info_b))

    print("── 绑定阶段 bg 亮度（R2b）──")
    import src.assets_manager as AM  # noqa: E402
    check("BG_MIN_FIRST_YAVG 已定义且 ≥32", float(AM.BG_MIN_FIRST_YAVG) >= 32, str(AM.BG_MIN_FIRST_YAVG))
    check("_first_frame_yavg 对暗素材给出低值",
          (AM._first_frame_yavg(dark) or 99) < AM.BG_MIN_FIRST_YAVG,
          str(AM._first_frame_yavg(dark)))
    check("_first_frame_yavg 对亮素材给出高值",
          (AM._first_frame_yavg(bright) or 0) >= AM.BG_MIN_FIRST_YAVG,
          str(AM._first_frame_yavg(bright)))

    print("── 渲染层不许再丢弃 open（R2a）──")
    tsx = (PROJ / "renderer/src/tech/TechStoryVideo.tsx").read_text(encoding="utf-8")
    check("TechStoryVideo 不再 cfg.open = null", "cfg.open = null" not in tsx)
    check("首行文字在 0–6 帧内淡入", "t(0, 6)" in tsx)
    check("标题在 2–16 帧内淡入", "t(2, 16)" in tsx)

    print("── 审批前门禁已接（R5）──")
    ex = (PROJ / "src/pipeline/executors.py").read_text(encoding="utf-8")
    check("exec_approval 里调 check_many", "check_many(_vids)" in ex)
    check("不合格抛 OPENING_TOO_DARK", "OPENING_TOO_DARK: 片头亮度不合格" in ex)

    print(f"\nOPENING_LUMA {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
