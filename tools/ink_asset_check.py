#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水墨 AI 素材校验器（Gemini/Veo 产出的 B-roll 验收）

用法：
    python3 ink_asset_check.py <file|dir> [...]

判据（针对水墨模板的素材位要求）：
  1. 规格：竖版优先 1080x1920 / 16:9 也可；≥4s；fps ≥ 24
  2. 墨色：画面低饱和（宣纸/墨），不能是彩色素材
  3. 有动：帧差能量 ≥ 1.0（静止素材不如不做）
  4. 无文字：OCR（chi_sim+eng）不能识别出成串字符 —— 有就是 AI 画了字/带水印
  5. 无黑边：外框不能是纯黑（否则裁切后出现黑框）
  6. 循环友好：首末帧 dHash 距离，越小越容易接成循环

退出码：0 全部 PASS/WARN；1 有 FAIL
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

def _pixels(img):
    """Pillow 14 起 getdata 弃用，优先用新 API，保持向后兼容。"""
    f = getattr(img, "get_flattened_data", None)
    return list(f()) if callable(f) else list(img.getdata())

TMP = Path(tempfile.mkdtemp(prefix="inkcheck_"))  # 注意：macOS /tmp 是软链，用真实路径
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    ).stdout
    try:
        j = json.loads(out)
    except Exception:
        return {}
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = 0.0
    try:
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = round(float(num) / float(den), 2) if float(den) else 0.0
    except Exception:
        pass
    return {
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": fps,
        "codec": v.get("codec_name"),
        "duration": round(float(j.get("format", {}).get("duration") or 0), 2),
        "mb": round(os.path.getsize(path) / 1e6, 2),
    }


def frames(path: Path, fps: float = 2.0) -> list[Image.Image]:
    d = TMP / f"f_{abs(hash(str(path))) % 10**8}"
    d.mkdir(exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale=480:-1",
         "-y", str(d / "f%04d.png")],
        check=False,
    )
    return [Image.open(p).convert("RGB") for p in sorted(d.glob("*.png"))]


def colorfulness(img: Image.Image) -> float:
    """中位饱和度（0-255）：水墨素材应很低"""
    small = img.resize((160, 284))
    ch = _pixels(small)
    sats = sorted(max(p) - min(p) for p in ch)
    return sats[len(sats) // 2]


def dhash(img: Image.Image) -> int:
    px = _pixels(img.convert("L").resize((17, 16)))
    bits = 0
    for r in range(16):
        for c in range(16):
            bits = (bits << 1) | (1 if px[r * 17 + c] > px[r * 17 + c + 1] else 0)
    return bits


def ocr_texts(imgs: list[Image.Image], min_conf: float = 78.0) -> tuple[str, str]:
    """抽 3 帧做 OCR。

    墨迹纹理会让 tesseract 产生大量「像字但不是字」的噪声（PR / ry / i.），
    所以这里用 TSV 输出 + 置信度过滤：只有 conf ≥ 78 且长度 ≥ 3 的词才算真文字。
    返回 (高置信文字, 低置信噪声样本) 便于排查。
    """
    strong, weak = [], []
    picks = [imgs[0], imgs[len(imgs) // 2], imgs[-1]] if len(imgs) >= 3 else imgs
    for i, im in enumerate(picks):
        p = TMP / f"ocr_{abs(hash(str(i))) % 10**6}_{len(strong)}.png"
        im.resize((im.width * 2, im.height * 2)).save(p)
        r = subprocess.run(
            ["tesseract", str(p), "stdout", "-l", "chi_sim+eng", "--psm", "11", "tsv"],
            capture_output=True, text=True,
        )
        for line in r.stdout.splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) < 12:
                continue
            word = (cols[11] or "").strip()
            try:
                conf = float(cols[10])
            except ValueError:
                continue
            if len(word) < 2 or word.isdigit():
                continue
            (strong if conf >= min_conf and len(word) >= 3 else weak).append(f"{word}({conf:.0f})")
    return " | ".join(dict.fromkeys(strong)), " | ".join(dict.fromkeys(weak))[:160]


def check(path: Path) -> dict:
    info = probe(path)
    imgs = frames(path)
    r: dict = {"file": path.name, **info, "issues": [], "warns": []}

    if not imgs:
        r["issues"].append("无法解码任何帧")
        return r

    w, h = info.get("width") or 0, info.get("height") or 0
    if w and h:
        if not ((h > w and abs(w / h - 9 / 16) < 0.02) or (w > h and abs(w / h - 16 / 9) < 0.02)):
            r["warns"].append(f"非 9:16 / 16:9 标准画幅（{w}x{h}），需裁切")
    if (info.get("duration") or 0) < 4:
        r["warns"].append(f"时长偏短 {info['duration']}s（<4s，循环素材建议 ≥6s）")
    if (info.get("fps") or 0) < 24:
        r["warns"].append(f"帧率偏低 {info['fps']}")

    # 运动量
    diffs = [
        ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean[0]
        for a, b in zip(imgs, imgs[1:])
    ]
    motion = round(sum(diffs) / len(diffs), 2) if diffs else 0.0
    r["motion"] = motion
    if motion < 1.0:
        r["issues"].append(f"画面几乎静止（帧差 {motion} < 1.0）")

    # 墨色
    sats = sorted(colorfulness(im) for im in imgs)
    sat = round(sats[len(sats) // 2], 1)
    r["saturation_median"] = sat
    if sat > 40:
        r["issues"].append(f"彩色素材（中位饱和 {sat} > 40），水墨模板需要单色墨")
    elif sat > 25:
        r["warns"].append(f"饱和度偏高 {sat}（墨色素材通常 ≤ 25）")

    # 亮度
    lum = ImageStat.Stat(imgs[len(imgs) // 2].convert("L")).mean[0]
    r["luma_mean"] = round(lum, 1)

    # 黑边
    edge_means = []
    for im in (imgs[0], imgs[len(imgs) // 2]):
        g = im.convert("L")
        ww, hh = g.size
        strips = [g.crop((0, 0, ww, max(2, hh // 50))), g.crop((0, hh - max(2, hh // 50), ww, hh)),
                  g.crop((0, 0, max(2, ww // 50), hh)), g.crop((ww - max(2, ww // 50), 0, ww, hh))]
        edge_means += [ImageStat.Stat(s).mean[0] for s in strips]
    if max(edge_means) < 20:
        r["issues"].append(f"四周纯黑边框（边缘均值 {max(edge_means):.0f}），裁切后会露黑边")
    r["edge_luma_max"] = round(max(edge_means), 1)

    # 文字 / 水印（只认高置信词，墨纹噪声不算）
    txt, noise = ocr_texts(imgs)
    r["ocr"] = txt[:200]
    r["ocr_noise"] = noise
    if txt:
        r["issues"].append(f"OCR 识别到文字（AI 画了字或带水印）：{txt[:60]}")

    # 循环友好
    loop = 1 - bin(dhash(imgs[0]) ^ dhash(imgs[-1])).count("1") / 256
    r["loop_similarity"] = round(loop, 2)
    if loop < 0.6:
        r["warns"].append(f"首末帧差异大（{loop}），循环接缝需淡入淡出处理")

    r["verdict"] = "FAIL" if r["issues"] else ("WARN" if r["warns"] else "PASS")
    return r


def main(argv: list[str]) -> int:
    targets: list[Path] = []
    for a in argv:
        p = Path(a).expanduser()
        if p.is_dir():
            targets += [f for f in sorted(p.rglob("*")) if f.suffix.lower() in VIDEO_EXT]
        elif p.exists():
            targets.append(p)
        else:
            print(f"✗ 找不到：{a}")
    if not targets:
        print("用法：python3 ink_asset_check.py <文件或目录> [...]")
        return 2

    bad = 0
    for f in targets:
        try:
            r = check(f)
        except Exception as e:  # noqa: BLE001
            print(f"\n{f.name}: 校验异常 {e}")
            bad += 1
            continue
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["verdict"]]
        print(f"\n{icon} {r['file']}  {r.get('width')}x{r.get('height')}  {r.get('duration')}s  "
              f"{r.get('fps')}fps  {r.get('mb')}MB")
        print(f"   运动={r.get('motion')}  中位饱和={r.get('saturation_median')}  "
              f"亮度={r.get('luma_mean')}  循环相似={r.get('loop_similarity')}")
        for i in r["issues"]:
            print(f"   ❌ {i}")
        for i in r["warns"]:
            print(f"   ⚠️ {i}")
        if r["verdict"] == "FAIL":
            bad += 1
    print(f"\n合计 {len(targets)} 个，FAIL {bad} 个")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
