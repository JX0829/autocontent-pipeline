#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BUGS-20261120 A3/A4 验收（只读，不产出成片）。

A3：连跑 5 个不同 seed 的素材绑定，首帧亮度（bg / open）必须 ≥ 32。
A4：触发一次真实的暗底失败，报错不得出现「竖版封面塞横屏」，必须带亮度指标。

跑法：python3 scripts/accept_opening_luma.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "scripts"))

from src.assets_manager import BG_MIN_FIRST_YAVG, AssetManager, _first_frame_yavg  # noqa: E402

PUBLIC = PROJ / "renderer" / "public"
PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def main() -> int:
    draft = json.loads((PROJ / "output/20261120/claude-ipo-1/draft_script.json").read_text(encoding="utf-8"))
    draft = draft.get("draft") or draft

    print(f"── A3：5 个 seed 的首帧亮度（下限 {BG_MIN_FIRST_YAVG:.0f}）──")
    rows = []
    for i in range(5):
        sel = AssetManager().select(run_id=f"a3-seed-{i+1}", draft=draft, locked=None)
        a = sel.to_audit()
        bg, op = (a.get("bg") or {}), (a.get("open") or {})
        bg_y = _first_frame_yavg(PUBLIC / str(bg.get("src") or "")) if bg.get("src") else None
        op_y = _first_frame_yavg(PUBLIC / str(op.get("src") or "")) if op.get("src") else None
        rows.append((i + 1, bg.get("id"), bg_y, op.get("id"), op_y))
        print(f"   seed{i+1}: bg={bg.get('id')} YAVG={bg_y} | open={op.get('id')} YAVG={op_y}")
        check(f"seed{i+1} bg 首帧 YAVG ≥ {BG_MIN_FIRST_YAVG:.0f}",
              bg_y is not None and bg_y >= BG_MIN_FIRST_YAVG, str(bg_y))
        check(f"seed{i+1} open 首帧 YAVG ≥ {BG_MIN_FIRST_YAVG:.0f}",
              op_y is not None and op_y >= BG_MIN_FIRST_YAVG, str(op_y))

    print("── A4：暗底失败的真实报错文案 ──")
    tmp = Path(tempfile.mkdtemp(prefix="a4_"))
    dark_v = tmp / "dark_16_9.mp4"
    subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=0x08090b:s=1920x1080:d=1.0:r=30",
                    "-pix_fmt", "yuv420p", "-y", str(dark_v)], check=True)
    dark_c = tmp / "dark_cover.png"
    subprocess.run(["/opt/homebrew/bin/ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=0x08090b:s=1920x1080:d=1", "-frames:v", "1", "-y", str(dark_c)], check=True)

    import landscape_guard as LG
    ok, rep = LG.check_landscape(str(dark_v), "", cover_path=str(dark_c), compare_cover_frame=False)
    reason = str(rep.get("reason") or rep.get("error") or "")
    print(f"   判定={ok} reason={reason[:160]}")
    check("暗底横版被拦下", ok is False)
    check("报错不再归因「竖版封面塞横屏」", "竖版封面塞横屏" not in reason, reason)
    check("报错带首帧近黑比", "首帧近黑" in reason, reason)
    check("报错带首帧 YAVG", "YAVG" in reason, reason)
    check("报告里带数值字段", rep.get("first_frame_ratio") is not None
          and rep.get("first_frame_yavg") is not None, str(rep.get("first_frame_ratio")))

    print(f"\nA3/A4 {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
