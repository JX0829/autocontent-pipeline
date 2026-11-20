#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""片头亮度验收（BUGS-20261120 R1/R5）：首帧近黑像素比 + 0–0.5s 暗段。

两个判据，都是「整屏」口径（不是平均亮度被局部亮区拉高就能过）：

1. **首帧近黑像素比 ≤ 40%** —— 用 `blackframe` 滤镜的 `pblack`（该帧低于阈值的像素百分比）。
2. **0–window 内不得出现 ≥ min_dur 的暗段** —— 用 `signalstats` 的 per-frame YAVG，
   连续 `YAVG <= y_max` 且累计时长 ≥ min_dur 即判 BAD。

为什么不能用「平均亮度」：`claude-ipo-1` 首帧近黑 93.3% 但画面里有一条亮边，
均值仍有 23 左右；反过来一条全是中等灰的片子均值 30 却没有任何可读内容。
近黑比才是「看起来是不是黑屏」的口径。

历史：这个脚本原来只活在 `/tmp/black_check.py`（小品 2026-11-20 手搓），
本单 R5 要求入库并在进入审批前自动跑，所以搬到 `src/utils/`，CLI 在 `scripts/`。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

FFMPEG = "/opt/homebrew/bin/ffmpeg"

#: 判定"近黑"的 Y 阈值（16 是标准 broadcast 黑；26 是实机片头经验值，见 BUGS-20261120 §4.2）
Y_MAX_DEFAULT = 26.0
#: 首帧近黑像素比上限（R1）
FIRST_FRAME_MAX_RATIO = 0.40
#: 片头观察窗（秒）与最短暗段（秒）
WINDOW_S = 0.5
DARK_MIN_DUR = 0.20


def _run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, errors="ignore")
    return (p.stdout or "") + (p.stderr or "")


def frame_luma(path: str | Path) -> list[tuple[float, float]]:
    """逐帧 (pts_time, YAVG)。"""
    out = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
                "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                "-an", "-f", "null", "-"])
    frames: list[tuple[float, float]] = []
    t: float | None = None
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if m and t is not None:
            frames.append((t, float(m.group(1))))
    return frames


def frame_black_ratio(path: str | Path, y_max: float = Y_MAX_DEFAULT) -> list[tuple[float, float]]:
    """逐帧 (pts_time, 近黑像素比 0~1)，来自 blackframe 的 pblack。"""
    # amount=0 → 任何含黑帧都上报；threshold 是像素 Y 阈值，与判据一致
    out = _run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
                "-vf", f"blackframe=amount=0:threshold={int(round(y_max))}",
                "-an", "-f", "null", "-"])
    rows: list[tuple[float, float]] = []
    for line in out.splitlines():
        if "pblack:" not in line:
            continue
        mt = re.search(r"t:([0-9.]+)", line)
        mp = re.search(r"pblack:([0-9.]+)", line)
        if mt and mp:
            rows.append((float(mt.group(1)), float(mp.group(1)) / 100.0))
    return rows


def check_opening(path: str | Path, *,
                  y_max: float = Y_MAX_DEFAULT,
                  first_max_ratio: float = FIRST_FRAME_MAX_RATIO,
                  window_s: float = WINDOW_S,
                  min_dur: float = DARK_MIN_DUR) -> dict[str, Any]:
    """片头是否合格。返回 ok / 指标 / 失败原因（可直接拼进报错文案）。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "code": "OPENING_CHECK_ERROR", "video": str(p),
                "reason": f"视频不存在: {p}"}

    luma = frame_luma(p)
    if not luma:
        return {"ok": False, "code": "OPENING_CHECK_ERROR", "video": str(p),
                "reason": "读不到帧亮度（ffmpeg signalstats 无输出）"}
    ratios = frame_black_ratio(p, y_max=y_max)

    fps_dt = ((luma[-1][0] - luma[0][0]) / max(1, len(luma) - 1)) if len(luma) > 1 else 0.033
    first_t, first_y = luma[0]
    first_ratio = next((r for r in ratios if abs(r[0] - first_t) < 0.02), None)
    first_ratio_v = first_ratio[1] if first_ratio else 0.0

    # 0–window 内的暗段
    win = [(t, y) for t, y in luma if t <= window_s]
    runs, cur = [], None
    for t, y in win:
        if y <= y_max:
            cur = {"start": t, "min": y, "n": 1} if cur is None else \
                  {"start": cur["start"], "min": min(cur["min"], y), "n": cur["n"] + 1}
        elif cur:
            runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    worst = max(runs, key=lambda r: r["n"] * fps_dt) if runs else None
    worst_dur = round(worst["n"] * fps_dt, 2) if worst else 0.0

    fails = []
    if first_ratio_v > first_max_ratio:
        fails.append(f"首帧近黑像素比 {first_ratio_v:.1%} > {first_max_ratio:.0%}")
    if worst_dur >= min_dur:
        _st = worst["start"] if worst else 0.0
        _mn = worst["min"] if worst else 0.0
        fails.append(f"片头 0–{window_s}s 存在 {worst_dur:.2f}s 暗段"
                     f"（start={_st:.2f}s minYAVG={_mn:.2f} ≤ {y_max:.0f}）")

    return {
        "ok": not fails,
        "code": "" if not fails else "OPENING_TOO_DARK",
        "video": str(p),
        "first_frame_ratio": round(first_ratio_v, 4),
        "first_frame_yavg": round(first_y, 2),
        "min_yavg_window": round(min((y for _, y in win), default=first_y), 2),
        "dark_run_s": worst_dur,
        "window_s": window_s,
        "reason": "；".join(fails),
    }


def check_many(paths: list[str | Path], **kw) -> dict[str, Any]:
    """竖版 + 横版各查一遍，任一不过即整体不过（R1「竖版、横版各自成立」）。"""
    results = [check_opening(p, **kw) for p in paths if str(p)]
    bad = [r for r in results if not r["ok"]]
    return {"ok": not bad, "results": results,
            "reason": "；".join(f"{Path(r['video']).name}: {r['reason']}" for r in bad)}


if __name__ == "__main__":  # pragma: no cover - CLI
    import sys
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(2)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    res = check_opening(args[0])
    print(json.dumps(res, ensure_ascii=False, indent=2) if as_json else
          f"{Path(res['video']).name}: OPENING_LUMA={'PASS' if res['ok'] else 'FAIL'} "
          f"首帧近黑比={res.get('first_frame_ratio')} 首帧YAVG={res.get('first_frame_yavg')} "
          f"片头暗段={res.get('dark_run_s')}s {res.get('reason') or ''}")
    raise SystemExit(0 if res["ok"] else 1)
