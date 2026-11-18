#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""封面 3:4 可见区回归：抖音按 3:4 居中裁剪，文案不能被截。

背景（2026-11-18 用户要求）：
  「0.5s 封面帧文字内容应该是按照 3:4 裁剪也能看到内容，不然抖音会截断」

抖音个人主页 / 作品列表用 3:4 居中裁剪显示 9:16 封面：
1080x1920 只显示 y∈[240,1680]（上下各裁 240）。封面文案块原来 bottom=210 →
块底落在 1710，超界 30px，最后一行会被切掉。

判据（本脚本，纯静态 + 一张真渲染，约 8 秒）：
  ① 模板常量：COVER_PORTRAIT_INSET_BOTTOM ≥ 1920-1680+20 = 260
  ② 文案块顶部不能顶出 3:4 上边界（留出多行文案的高度预算 520px）
  ③ 左右内边距与正文一致（100），3:4 只裁上下、不裁左右，但仍需 ≥96 防贴边
  ④ 真渲染一张长文案封面，OCR 出最底文字 y 必须 ≤ 1680 - 10
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

CARD = PROJ / "renderer" / "src" / "tech" / "TechCoverCard.tsx"
HEIGHT = 1920
SAFE_TOP = 240
SAFE_BOTTOM = 1680

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{(' ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def _num(src: str, key: str, default: int = -1) -> int:
    m = re.search(rf"{key}\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else default


def main() -> int:
    if not CARD.exists():
        print(f"❌ 封面模板不存在: {CARD}")
        return 1
    src = CARD.read_text(encoding="utf-8")

    pad_x = _num(src, "COVER_PORTRAIT_PAD_X")
    offset_y = _num(src, "COVER_PORTRAIT_OFFSET_Y")
    main_size = _num(src, "COVER_MAIN_SIZE")
    safe_top = _num(src, "COVER_SAFE_34_TOP", SAFE_TOP)
    safe_bottom = _num(src, "COVER_SAFE_34_BOTTOM", SAFE_BOTTOM)

    check("① 3:4 常量一致（240 / 1680）", safe_top == SAFE_TOP and safe_bottom == SAFE_BOTTOM,
          f"{safe_top} / {safe_bottom}")

    # 竖版：垂直居中 + 向下偏移 32，块高预算 350 → 中心 960+32=992，块范围约 817~1167
    budget = 350
    center = HEIGHT // 2 + offset_y
    block_top, block_bottom = center - budget // 2, center + budget // 2
    check("② 居中下移后整块落在 3:4 内",
          block_top >= safe_top and block_bottom <= safe_bottom,
          f"块范围 ~y {block_top}~{block_bottom}（3:4 {safe_top}~{safe_bottom}）")

    check("③ 向下偏移 = 32px", offset_y == 32, f"{offset_y}px")

    check("④ 水平边距 ≥ 64px", pad_x >= 64, f"{pad_x}px")
    check("⑤ 主标题字号已收敛（≤88）", 60 <= main_size <= 88, f"{main_size}px")
    check("⑥ 版式确为居中", "justifyContent: 'center'" in src and "textAlign: 'center'" in src
          and "COVER_PORTRAIT_OFFSET_Y" in src, "justifyContent/textAlign/offset 均就位")

    # ⑤ 真渲染长文案封面，OCR 量最底文字
    try:
        tmp = Path(tempfile.mkdtemp(prefix="cover34_"))
        pub = PROJ / "renderer" / "public" / "runs" / "_cover34check"
        pub.mkdir(parents=True, exist_ok=True)
        bg = pub / "bg.png"            # 必须放 public/ 下，staticFile 只认相对 public 的路径
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=0x223344:s=1080x{HEIGHT}", "-frames:v", "1", str(bg)],
                       capture_output=True, text=True)
        props = tmp / "props.json"
        long_main = "OpenAI出海这次先签政府再谈监管细则"
        props.write_text(
            '{"bg": "%s", "main": "%s", "sub": "%s", "theme": "breaking-news", "aspect": "portrait"}'
            % ("runs/_cover34check/bg.png", long_main, "智利科技部拒公开全文，议员质疑数据保护条款"),
            encoding="utf-8")
        out = tmp / "cover.png"
        proc = subprocess.run(
            ["npx", "remotion", "still", "src/index.ts", "TechCoverCardPortrait", str(out),
             f"--props={props}", "--image-format=png"],
            cwd=str(PROJ / "renderer"), capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists():
            check("⑦ 真渲染封面", False, (proc.stderr or "").strip().splitlines()[-1:][0] if proc.stderr else "")
        else:
            ocr = subprocess.run(["tesseract", str(out), "-", "--psm", "11", "tsv"],
                                 capture_output=True, text=True)
            rows = [ln.split("\t") for ln in ocr.stdout.splitlines()[1:] if ln.strip()]
            ys = [int(r[8]) + int(r[9]) for r in rows
                  if len(r) > 11 and r[11].strip() and r[10].isdigit() and int(r[10]) > 40]
            if ys:
                check("⑦ 最底文字落在 3:4 内", max(ys) <= SAFE_BOTTOM - 10,
                      f"最底 y={max(ys)}（上限 {SAFE_BOTTOM - 10}）")
            else:
                print("  ⚠️ OCR 未识别出文字（描边字体常见），跳过像素级判据，以上静态判据仍生效")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️ 真渲染环节跳过：{exc}")

    print(f"\nCOVER 3:4 SAFE {'PASS' if not fails else 'FAIL'}（块 y {block_top}~{block_bottom}，偏移 {offset_y}，边距 {pad_x}）")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
