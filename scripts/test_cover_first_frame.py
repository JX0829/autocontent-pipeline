#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面口径回归：三平台统一「首帧即封面」，不再单独上传。

正向：真实 run 的 video.mp4（已拼 0.5s 封面片头）vs cover.png → ok，SSIM ≥ 0.85
反向：拿另一条片的 cover.png 去比 → 必须失败（COVER_LEAD_IN_MISSING）
边界：视频/封面文件缺失 → 明确错误码，不静默
"""
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.utils.frame_match import check_video_starts_with_cover  # noqa: E402

PASS = 0
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}" + (f" | {extra}" if extra else ""))
    else:
        FAIL.append(name)
        print(f"FAIL  {name}" + (f" | {extra}" if extra else ""))


runs = sorted(PROJ.glob("output/*/gemini4neon-1"))
if not runs:
    print("SKIP: 没有 gemini4neon-1 run 可用于真值测试")
    sys.exit(1)
run = runs[-1]
video, cover = run / "video.mp4", run / "cover.png"
print(f"样本 run={run.name} video={video.exists()} cover={cover.exists()}")

r1 = check_video_starts_with_cover(video, cover)
check("① 首帧即封面通过", r1["ok"], r1["detail"])
check("① SSIM ≥ 0.85", (r1.get("ssim") or 0) >= 0.85, f"ssim={r1.get('ssim')}")

others = [p for p in PROJ.glob("output/*/*/cover.png") if p.parent != run]
if others:
    r2 = check_video_starts_with_cover(video, others[0])
    check("② 换一张封面必须失败", not r2["ok"], f"{r2['code']} ssim={r2.get('ssim')}")
    check("② 失败码是 COVER_LEAD_IN_MISSING", r2["code"] == "COVER_LEAD_IN_MISSING", r2["code"])

r3 = check_video_starts_with_cover(run / "nope.mp4", cover)
check("③ 视频缺失明确报错", not r3["ok"] and r3["code"] == "VIDEO_MISSING", r3["code"])

r4 = check_video_starts_with_cover(video, run / "nope.png")
check("④ 封面缺失明确报错", not r4["ok"] and r4["code"] == "COVER_MISSING", r4["code"])

print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
sys.exit(1 if FAIL else 0)
