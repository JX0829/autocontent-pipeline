#!/usr/bin/env python3
"""A1/A2/A5 抽帧实测：底板大字的字号 / 底板尺寸 / 底板不透明度（纯 PIL，无 numpy）。

为什么要「两路视频相减」算不透明度：
    底板是半透明矩形压在**真实画面**上，单看一帧无法反解 alpha（不知道底层像素）。
    所以在同一 props 下分别渲 with-plate / without-plate 两版，同一帧逐像素相减：
        L_on = α·L_plate + (1-α)·L_off
        ⇒ α  = (L_on - L_off) / (L_plate - L_off)
    底板的 L_plate 由代码常量 `rgba(5,8,16,α)` 给出 → 反解出的 α 就是**实测不透明度**。

用法：
    python3 scripts/measure_plate.py --on /tmp/plate_p.mp4 --off /tmp/plate_off.mp4 \
        --aspect portrait --frames 30,60,90,120,150,175 --out /tmp/a1_plate.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

PLATE_RGB = (5, 8, 16)          # 与 TechStoryVideo 的 `rgba(5,8,16,α)` 一致
CAP_RATIO = 0.94                # 汉字字面高 ≈ fontSize × 0.94（真机标定：104px 字 → 98px 行带）
DARK = 60                       # 底板像素阈值（L ≤ 60）
BRIGHT = 200                    # 文字像素阈值


def grab(video: Path, n: int, dst: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video),
         "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(dst)],
        check=True,
    )
    return dst


def grayscale(path: Path) -> tuple[bytes, int, int]:
    im = Image.open(path).convert("L")
    return im.tobytes(), im.size[0], im.size[1]


def find_plate(buf: bytes, w: int, h: int) -> dict:
    """底板 = 底部区域里连续的「暗底 + 白字」横条；宽度按**暗像素跨度**算，不按整行覆盖率。

    真机教训：底板宽度是按文字内容收缩的（5 字 ≈ 660px，只占 1080 的 61%），
    用「整行 80% 都是暗像素」判会把底板漏掉，反而把画面底部的暗区当底板。
    """
    top = int(h * 0.45)
    spans: list[tuple[int, int, int]] = []      # (y, x0, x1)
    for y in range(top, h):
        row = buf[y * w:(y + 1) * w]
        xs = [x for x in range(0, w, 2) if row[x] <= DARK]
        if len(xs) < 10:
            continue
        x0, x1 = xs[0], xs[-1]
        span = x1 - x0 + 1
        if span < w * 0.25:                      # 太窄的不算底板
            continue
        inside = sum(1 for x in range(x0, x1 + 1, 2) if row[x] <= DARK)
        if inside / max(1, span // 2) < 0.45:    # 跨度内暗像素占比（文字/图形会把洞打穿）
            continue
        if x0 <= 0 and x1 >= w - 2:              # 顶到画面两侧 → 是背景，不是底板
            continue
        spans.append((y, x0, x1))
    if not spans:
        return {"found": False}
    groups, cur = [], [spans[0]]
    for s in spans[1:]:
        if s[0] - cur[-1][0] > 4:
            groups.append(cur)
            cur = [s]
        else:
            cur.append(s)
    groups.append(cur)
    g = max(groups, key=len)
    y0, y1 = g[0][0], g[-1][0]
    x0 = min(s[1] for s in g)
    x1 = max(s[2] for s in g)
    return {"found": True, "x0": int(x0), "x1": int(x1), "y0": int(y0), "y1": int(y1),
            "width": int(x1 - x0 + 1), "height": int(y1 - y0 + 1),
            "bottom_gap": int(h - (y1 + 1))}


def text_bands(buf: bytes, w: int, plate: dict) -> list[dict]:
    if not plate.get("found"):
        return []
    y0, y1, x0, x1 = plate["y0"], plate["y1"], plate["x0"], plate["x1"]
    rows = []
    for y in range(y0, y1 + 1):
        row = buf[y * w + x0:y * w + x1 + 1]
        if sum(1 for v in row if v >= BRIGHT) >= 3:
            rows.append(y)
    if not rows:
        return []
    bands, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > 2:
            bands.append((start, prev))
            start = r
        prev = r
    bands.append((start, prev))
    out = []
    for a, b in bands:
        hgt = b - a + 1
        if hgt < 10:
            continue
        out.append({"y": a, "height": hgt, "implied_font_px": round(hgt / CAP_RATIO, 1)})
    return out


def alpha_estimate(on: bytes, off: bytes, w: int, plate: dict) -> dict:
    """两路相减反解底板 alpha（只用被底板盖住、且底层比底板亮的像素）。"""
    if not plate.get("found"):
        return {"ok": False, "reason": "未找到底板"}
    inset = 6
    plate_l = 0.2126 * PLATE_RGB[0] + 0.7152 * PLATE_RGB[1] + 0.0722 * PLATE_RGB[2]
    vals = []
    for y in range(plate["y0"] + inset, plate["y1"] - inset, 2):
        base = y * w
        for x in range(plate["x0"] + inset, plate["x1"] - inset, 2):
            lo = off[base + x]
            lo_on = on[base + x]
            if lo > plate_l + 8:                     # 底层比底板亮才能反解
                a = (lo_on - lo) / (plate_l - lo)
                if 0 < a <= 1.05:
                    vals.append(a)
    if len(vals) < 300:
        return {"ok": False, "reason": "有效样本不足", "samples": len(vals)}
    vals.sort()
    med = vals[len(vals) // 2]
    p10 = vals[int(len(vals) * 0.1)]
    return {"ok": True, "alpha_median": round(med, 3), "alpha_p10": round(p10, 3),
            "samples": len(vals)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", required=True)
    ap.add_argument("--off", default="")
    ap.add_argument("--aspect", default="portrait", choices=["portrait", "landscape"])
    ap.add_argument("--frames", default="30,60,90,120,150")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    want = [int(x) for x in a.frames.split(",") if x.strip()]
    off_v = Path(a.off) if a.off else None
    tmp = Path(tempfile.mkdtemp(prefix="plate_measure_"))
    rows: list[dict] = []
    for n in want:
        p_on = grab(Path(a.on), n, tmp / f"on_{n}.png")
        buf_on, w, h = grayscale(p_on)
        plate = find_plate(buf_on, w, h)
        bands = text_bands(buf_on, w, plate)
        rec = {"frame": n, "plate": plate, "text_lines": bands,
               "max_implied_font_px": max((b["implied_font_px"] for b in bands), default=None)}
        if off_v:
            buf_off, _, _ = grayscale(grab(off_v, n, tmp / f"off_{n}.png"))
            rec["alpha"] = alpha_estimate(buf_on, buf_off, w, plate)
        rows.append(rec)

    spec_bottom = 320 if a.aspect == "portrait" else 200
    spec_font = 104 if a.aspect == "portrait" else 88
    gaps = [r["plate"].get("bottom_gap") for r in rows if r["plate"].get("found")]
    fonts = [r["max_implied_font_px"] for r in rows if r["max_implied_font_px"]]
    alphas = sorted(r["alpha"]["alpha_median"] for r in rows
                    if isinstance(r.get("alpha"), dict) and r["alpha"].get("ok"))
    summary = {
        "aspect": a.aspect,
        "spec": {"font_px": spec_font, "bottom_px": spec_bottom,
                 "tolerance_px": 20, "opacity_min": 0.92},
        "measured": {
            "max_font_px": max(fonts) if fonts else None,
            "all_font_px": fonts,
            "bottom_gap_px": sorted(gaps),
            "alpha_median": alphas[len(alphas) // 2] if alphas else None,
        },
        "verdict": {
            "font_ok": bool(fonts) and max(fonts) >= spec_font * 0.95,
            "bottom_ok": bool(gaps) and all(abs(g - spec_bottom) <= 20 for g in gaps),
            "opacity_ok": bool(alphas) and alphas[len(alphas) // 2] >= 0.92,
        },
        "frames": rows,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[{a.aspect}] font={summary['measured']['max_font_px']}px (spec≥{spec_font}) "
          f"bottom={summary['measured']['bottom_gap_px']} (spec {spec_bottom}±20) "
          f"alpha={summary['measured']['alpha_median']} (spec≥0.92) → {summary['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
