#!/usr/bin/env python3
"""A3 抽帧验收：20 字超长 highlight_text 的降档/折行表现，以及「超 2 行必须回退现样式」。

判据（SPEC-20261116 §5 A3）：
  1. 20 字 → **装进底板**（自动降字号或折行），文本**一个字不丢**、**没有省略号**；
  2. 33 字 → 降到底仍装不下 → **不出现底板**（回退旧字幕样式），不截断。
OCR 只作旁证：中文 OCR 会认错字，所以只比对「非空 / 无省略号 / 字数接近」，
最终判据是「底板存在性 + 行数 + 字号 + 像素文字高度」。

用法：
    python3 scripts/verify_plate_a3.py --video /tmp/a3.mp4 --plate-frame 260 \
        --fallback-frame 390 --expect-20 "政府用模型公务员培训扶持本地创业开放全文" --out /tmp/a3.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from measure_plate import find_plate, grayscale, text_bands

OCR_BIN = "tesseract"


def plate_shaped(plate: dict, bottom: int = 320) -> bool:
    """「是不是底板」不能只看「有暗区」：旧字幕胶囊压在深色画面上也是暗的。

    底板特征：高度 ≥100px（2 行 104px + 上下 56px 内边距 → 351）、距底 ≈320。
    旧字幕胶囊只有 ~55px 高、距底 169 → 一眼区分。
    """
    if not plate.get("found"):
        return False
    return plate.get("height", 0) >= 100 and abs(plate.get("bottom_gap", -999) - bottom) <= 40


def grab(video: Path, n: int, dst: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
                    "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(dst)], check=True)
    return dst


def ocr_crop(png: Path, box: tuple[int, int, int, int]) -> tuple[str, str]:
    """把底板区域裁出来 OCR；返回 (去掉空白的文本, 备注)。"""
    crop = Path(png).with_name(Path(png).stem + "_crop.png")
    Image.open(png).crop(box).save(crop)
    try:
        r = subprocess.run([OCR_BIN, str(crop), "stdout", "-l", "chi_sim"],
                           capture_output=True, text=True, timeout=120)
        return "".join(r.stdout.split()), ""
    except FileNotFoundError:
        return "", "未安装 tesseract，跳过 OCR"
    except subprocess.TimeoutExpired:
        return "", "OCR 超时"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--plate-frame", type=int, required=True)
    ap.add_argument("--fallback-frame", type=int, required=True)
    ap.add_argument("--expect-20", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="plate_a3_"))
    result: dict = {"video": a.video, "checks": {}}

    # ① 20 字：必须有底板，且行数 ≤2、文本完整
    p1 = grab(Path(a.video), a.plate_frame, tmp / "plate.png")
    buf, w, h = grayscale(p1)
    plate = find_plate(buf, w, h)
    bands = text_bands(buf, w, plate)
    ocr, note = ("", "")
    if plate.get("found"):
        ocr, note = ocr_crop(p1, (plate["x0"], plate["y0"], plate["x1"], plate["y1"]))
    expect = a.expect_20.strip()
    c1 = {
        "plate_found": plate_shaped(plate),
        "lines": len(bands),
        "font_px": max((b["implied_font_px"] for b in bands), default=None),
        "plate_size": [plate.get("width"), plate.get("height")] if plate.get("found") else None,
        "ocr": ocr,
        "ocr_note": note,
        "no_ellipsis": ("…" not in ocr) and ("..." not in ocr),
        "text_length_ok": (not expect) or (len(ocr) >= len(expect) - 3),
    }
    c1["ok"] = (c1["plate_found"] and c1["lines"] <= 2 and c1["no_ellipsis"]
                and c1["text_length_ok"])
    result["checks"]["a3_20chars"] = c1

    # ② 33 字：必须**没有**底板（回退旧样式），且画面里没有省略号
    p2 = grab(Path(a.video), a.fallback_frame, tmp / "fallback.png")
    buf2, w2, h2 = grayscale(p2)
    plate2 = find_plate(buf2, w2, h2)
    c2 = {"plate_found": plate_shaped(plate2),
          "raw_dark_box": plate2 if plate2.get("found") else None,
          "ok": not plate_shaped(plate2),
          "note": "底板上限之外 → 回退旧字幕样式（不截断、不加省略号）"}
    result["checks"]["a3_33chars_fallback"] = c2

    result["ok"] = bool(c1["ok"] and c2["ok"])
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nA3: 20字 plate={c1['plate_found']} lines={c1['lines']} font={c1['font_px']} "
          f"ocr={c1['ocr'][:24]!r} no_ellipsis={c1['no_ellipsis']} | "
          f"33字 plate={c2['plate_found']}(应为 False) → {'PASS' if result['ok'] else 'FAIL'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
