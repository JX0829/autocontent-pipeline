#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""竖版安全边距巡检（静态，<0.1s）。

背景（2026-11-18 用户反馈）：「抖音视频整个内容被扩展到屏幕内侧，视频号有边距抖音没有」。
根因：抖音信息流把竖版左右各裁掉约 9%，模板原来的 88/70px 被裁完就贴边了。
修法：`renderer/src/tech/TechStoryVideo.tsx` 引入 SAFE_X_VERTICAL=120，竖版所有文字层
统一吃它（2026-11-18 折中定 100px）；横版 1920 宽保持 88 不变。

本脚本防的是「有人手滑把 88 写回去」——静态扫描，不渲染。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
TSX = PROJ / "renderer" / "src" / "tech" / "TechStoryVideo.tsx"

MIN_VERTICAL = 100
ALLOWED_LANDSCAPE = 88

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{(' ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def main() -> int:
    if not TSX.exists():
        print(f"❌ 模板不存在: {TSX}")
        return 1
    src = TSX.read_text(encoding="utf-8")

    m = re.search(r"const\s+SAFE_X_VERTICAL\s*=\s*(\d+)", src)
    vert = int(m.group(1)) if m else 0
    check(f"竖版安全边距 ≥ {MIN_VERTICAL}px", vert >= MIN_VERTICAL, f"实际 {vert}px（{vert / 1080:.1%}）")

    m2 = re.search(r"const\s+SAFE_X_LANDSCAPE\s*=\s*(\d+)", src)
    land = int(m2.group(1)) if m2 else -1
    check("横版边距未被顺手改坏", land == ALLOWED_LANDSCAPE, f"实际 {land}px")

    check("safeX hook 已接入竖版文字层", src.count("useSafeX()") >= 4,
          f"useSafeX() 调用 {src.count('useSafeX()')} 处")

    hard = re.findall(r"left:\s*(?:88|70|6[0-9])", src)
    check("没有残留硬编码贴边值（88/70/6x）", not hard, f"命中 {hard}")

    print(f"\nSAFE AREA {'OK' if not fails else 'FAIL'}"
          f"（竖版 {vert}px / 横版 {land}px）")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
