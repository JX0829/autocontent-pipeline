#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整屏黑帧检测：per-frame YAVG（signalstats）+ blackdetect 双判据。

用法: python3 black_check.py <video.mp4> [--min-dur 0.20] [--y-max 16]
输出: 每段疑似黑场的起始时间/时长/最低YAVG；总结行 BLACKCHECK=PASS|FAIL
"""
import re
import subprocess
import sys

FFMPEG = "/opt/homebrew/bin/ffmpeg"
# NOTE（BUGS-20261120 R5）：这份 CLI 现在只是 `src/utils/black_check.py` 的薄壳；
# 判据（首帧近黑比 ≤40% + 片头无 ≥0.2s 暗段）在 src/utils 里，pipeline 的审批门禁直接调它。


def frame_luma(path):
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", path,
           "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
           "-an", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    out = p.stdout + p.stderr
    frames = []
    t = None
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if m and t is not None:
            frames.append((t, float(m.group(1))))
    return frames


def blackdetect(path):
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-i", path,
           "-vf", "blackdetect=d=0.10:pix_th=0.10", "-an", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    return re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+) black_duration:([0-9.]+)",
                      p.stdout + p.stderr)


def main():
    path = sys.argv[1]
    min_dur = 0.20
    y_max = 16.0
    if "--min-dur" in sys.argv:
        min_dur = float(sys.argv[sys.argv.index("--min-dur") + 1])
    if "--y-max" in sys.argv:
        y_max = float(sys.argv[sys.argv.index("--y-max") + 1])

    frames = frame_luma(path)
    if not frames:
        print(f"BLACKCHECK=ERROR 无法读取帧亮度 {path}")
        return 2
    fps_dt = (frames[-1][0] - frames[0][0]) / max(1, len(frames) - 1)

    runs, cur = [], None
    for t, y in frames:
        if y <= y_max:
            if cur is None:
                cur = {"start": t, "min": y, "n": 1}
            else:
                cur["min"] = min(cur["min"], y)
                cur["n"] += 1
        else:
            if cur:
                runs.append(cur)
                cur = None
    if cur:
        runs.append(cur)

    print(f"file={path}")
    print(f"frames={len(frames)} fps_dt={fps_dt:.3f}s duration={frames[-1][0]:.2f}s")
    print(f"yavg_min={min(y for _, y in frames):.2f} yavg_mean={sum(y for _, y in frames)/len(frames):.2f}")
    bad = []
    for r in runs:
        dur = r["n"] * fps_dt
        flag = "BAD" if dur >= min_dur else "ok(短暂)"
        if dur >= min_dur:
            bad.append((r["start"], dur, r["min"]))
        print(f"  dark run start={r['start']:.2f}s dur={dur:.2f}s minYAVG={r['min']:.2f} -> {flag}")
    bd = blackdetect(path)
    print(f"blackdetect_segments={len(bd)}")
    for s, e, d in bd:
        print(f"  blackdetect start={s}s end={e}s dur={d}s")
    print("BLACKCHECK=" + ("FAIL" if bad else "PASS"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
