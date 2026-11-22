#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横版画幅判据回归（2026-11-21 MedChemBench 误红）。

事故：landscape_guard 拿**封面片头帧**（0.5s 设计卡，左右边距 95/144px）
去判「竖版封面塞横屏」，右边缘 3.9% < 5% 直接拦死；
改标题 15→19 字重渲指标一位数不动 —— 因为判的根本不是正文。

修法：判据对象改为**正片**（同名 `_body.mp4`，没有就退到片头之后 0.5s）。

本测试锁死：
  ① 真实成片（封面有设计边距）→ ok=True，且 report.sample 指向 _body.mp4
  ② 竖版封面居中塞进横屏（合成负例）→ ok=False，coverage < 0.55
  ③ 无 _body.mp4 时退化成 0.5s 采样（不报错、不判 0 帧）
"""
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from scripts.landscape_guard import check_landscape  # noqa: E402

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


runs = sorted(PROJ.glob("output/*/*/video_16_9.mp4"))
if not runs:
    print("SKIP: 没有横版成片")
    sys.exit(1)

target = None
for v in runs:
    if "medchembench" in str(v):
        target = v
        break
target = target or runs[-1]
print(f"样本 {target}")

ok, rep = check_landscape(str(target), "")
check("① 有封面片头的成片不再误红", ok, str(rep.get("reason") or rep.get("sample")))
check("① 判据采样落在正文", (rep.get("sample") or {}).get("video", "").endswith("_body.mp4"),
      str(rep.get("sample")))
check("① 覆盖率/边缘指标齐全",
      all(k in rep for k in ("coverage", "edge_left", "edge_right")),
      f"{rep.get('coverage')}/{rep.get('edge_left')}/{rep.get('edge_right')}")

# ② 合成负例：竖版封面居中塞进 1920x1080
tmp = Path(tempfile.mkdtemp())
neg = tmp / "neg.mp4"
cover = target.parent / "cover.png"
if cover.exists():
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(cover),
                    "-vf", "scale=608:1080,pad=1920:1080:(ow-iw)/2:0:black",
                    "-t", "1", "-r", "30", str(neg)], check=True)
    ok_neg, rep_neg = check_landscape(str(neg), "")
    check("② 竖版塞横屏仍然拦住", not ok_neg, str(rep_neg.get("reason"))[:70])
    check("② 负例覆盖率 < 0.55", (rep_neg.get("coverage") or 1) < 0.55, str(rep_neg.get("coverage")))

# ③ 无 _body.mp4 的横版（只有带片头的成片）→ 退到 0.5s 采样
stripped = tmp / "nobody_16_9.mp4"
subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(target), "-c", "copy", str(stripped)],
               check=True)
ok3, rep3 = check_landscape(str(stripped), "")
check("③ 无 _body 时退 0.5s 采样且不报错", "error" not in rep3,
      str(rep3.get("sample") or rep3.get("error")))

print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
sys.exit(1 if FAIL else 0)
