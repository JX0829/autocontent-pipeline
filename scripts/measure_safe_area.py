#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A9 安全边距量测：竖版成片里**文字像素**是否越界（x<120 / x>960）。

原理：文字层是浅色（白/AIACCENT）压在深色底上。把亮像素（L≥160）当文字候选，
取其外接框，检查是否落入抖音 feed 裁切区（左右各裁 ~97px，这里留到 120px 更保守）。

用法：
  python3 scripts/measure_safe_area.py frame.png [--threshold 160] [--limit 120]
输出：JSON（bbox / 越界像素数 / PASS|FAIL），退出码 0=通过。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frame")
    ap.add_argument("--threshold", type=int, default=160, help="亮度阈值（0-255）")
    ap.add_argument("--limit", type=int, default=120, help="左右安全边距（px）")
    ap.add_argument("--min-bright", type=int, default=200,
                    help="亮像素总数下限（防「全黑帧」被当成通过）")
    ap.add_argument("--baseline", default="",
                    help="同帧的「无文字」基准图；给了就用差分取文字掩膜（更准，避开进度条/网格线）")
    ap.add_argument("--diff", type=int, default=28, help="差分阈值")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    from PIL import Image

    im = Image.open(a.frame).convert("L")
    w, h = im.size
    px = im.load()
    base = None
    if a.baseline:
        b = Image.open(a.baseline).convert("L")
        if b.size != im.size:
            raise SystemExit(f"基准图尺寸不符：{b.size} != {im.size}")
        base = b.load()
    left = right = 0
    min_x, max_x = w, -1
    bright = 0
    for y in range(h):
        for x in range(w):
            is_text = (px[x, y] >= a.threshold) if base is None else \
                      (abs(px[x, y] - base[x, y]) >= a.diff and px[x, y] > base[x, y])
            if is_text:
                bright += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if x < a.limit:
                    left += 1
                elif x > w - a.limit - 1:
                    right += 1
    res = {
        "frame": a.frame, "size": [w, h], "threshold": a.threshold, "limit": a.limit,
        "bright_pixels": bright, "bbox_x": [min_x if bright else None, max_x if bright else None],
        "left_violation_px": left, "right_violation_px": right,
        "ok": bool(bright >= a.min_bright and left == 0 and right == 0),
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
