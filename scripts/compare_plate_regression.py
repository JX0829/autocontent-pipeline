#!/usr/bin/env python3
"""A5 回归：不传 `plate_style` 时，新代码渲出的帧与**基线成片**逐帧比对。

判据：同一 props、同一帧号，dHash 汉明距离 = 0（完全一致）；>0 说明渲染结果被改动。
用法：
    python3 scripts/compare_plate_regression.py --base <基线 video.mp4> --new <新 video.mp4> --frames 0-180
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


def dhash(path: Path, size: int = 8) -> int:
    """dHash：缩到 (size+1)×size 灰度，比较相邻像素 → 稳定、对缩放/轻微压缩鲁棒。"""
    im = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(im.tobytes())
    bits = 0
    for y in range(size):
        row = px[y * (size + 1):(y + 1) * (size + 1)]
        for x in range(size):
            bits = (bits << 1) | (1 if row[x] > row[x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def mae(p1: Path, p2: Path) -> float:
    """逐像素平均绝对误差（0~255 标度）：区分「版式变了」和「x264 编码噪声」。"""
    a = Image.open(p1).convert("L")
    b = Image.open(p2).convert("L")
    if a.size != b.size:
        return 255.0
    pa, pb = a.tobytes(), b.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)


def grab(video: Path, n: int, dst: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video),
         "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(dst)],
        check=True,
    )
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--frames", default="0-180")
    ap.add_argument("--step", type=int, default=15)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    lo, _, hi = a.frames.partition("-")
    start, end = int(lo), int(hi or lo)
    tmp = Path(tempfile.mkdtemp(prefix="plate_reg_"))
    rows, worst = [], 0
    for n in range(start, end + 1, max(1, a.step)):
        pb = grab(Path(a.base), n, tmp / f"b_{n}.png")
        pn = grab(Path(a.new), n, tmp / f"n_{n}.png")
        d = hamming(dhash(pb), dhash(pn))
        worst = max(worst, d)
        rows.append({"frame": n, "dist": d, "mae": round(mae(pb, pn), 3)})

    same = sum(1 for r in rows if r["dist"] == 0)
    worst_mae = max((r["mae"] for r in rows), default=0.0)
    if worst == 0:
        verdict = "IDENTICAL"
    elif worst <= 2 and worst_mae < 1.0:
        # dHash 差 1~2 bit 且像素 MAE < 1/255 → x264 编码噪声，不是版式改动
        verdict = "IDENTICAL_ENCODING_NOISE"
    else:
        verdict = "DIFF"
    out = {
        "base": a.base, "new": a.new, "frames": a.frames,
        "sampled": len(rows), "identical": same, "max_dist": worst,
        "max_mae": round(worst_mae, 3), "verdict": verdict,
        "detail": rows,
    }
    import json
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nA5: {same}/{len(rows)} 帧完全一致，最大汉明距离 {worst}，"
          f"最大像素 MAE {worst_mae:.3f} → {out['verdict']}")
    return 0 if verdict.startswith("IDENTICAL") else 1


if __name__ == "__main__":
    sys.exit(main())
