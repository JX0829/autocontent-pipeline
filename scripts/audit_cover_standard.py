#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""封面版式统一巡检（**所有模板通用**，静态 + 极快）。

2026-11-18 用户定稿的封面规范（一句话版）：
  「居中布局、向下偏移 32、字体小一点、水平边距最小 64；上下内容隐藏，图面干净吸引人；
   按 3:4 裁剪也能看到内容；底图不许带正片文字。」

这条规范**不是 tech_story 专属**。本脚本保证三件事：
  ① 单一事实源：`renderer/src/tech/coverFrame.ts` 的常量值就是规范值；
  ② 新模板封面必须复用这组常量（或显式标 LEGACY-COVER 认账），防止有人另抄一份数字；
  ③ 底图来源必须是「无字素材/背景 loop」，成片截帧只做兜底（由 cover_compose 保证）。

跑法：python3 scripts/audit_cover_standard.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
SRC = PROJ / "renderer" / "src"
FRAME = SRC / "tech" / "coverFrame.ts"

#: 规范值（改这里等于改规范，必须同步 coverFrame.ts + 本脚本 + RUNBOOK）
EXPECT = {
    "COVER_SAFE_34_TOP": 240,
    "COVER_SAFE_34_BOTTOM": 1680,
    "COVER_PAD_X": 64,
    "COVER_OFFSET_Y": 32,
    "COVER_MAIN_SIZE": 76,
    "COVER_SUB_SIZE": 28,
    "COVER_KICKER_SIZE": 20,
    "COVER_BAND_HEIGHT": 240,
}

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{(' ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def num(src: str, key: str) -> int:
    m = re.search(rf"{key}\s*=\s*(\d+)", src)
    return int(m.group(1)) if m else -1


def cover_components() -> list[Path]:
    """所有封面组件：文件名含 Cover，或导出名含 Cover/Still 的组件文件。"""
    out: list[Path] = []
    root = SRC / "Root.tsx"
    root_src = root.read_text(encoding="utf-8") if root.exists() else ""
    for f in sorted(SRC.rglob("*.tsx")):
        if f.name in ("Root.tsx", "index.tsx"):   # 注册表不是组件
            continue
        name = f.name
        if "Cover" in name or "Cover" in f.stem:
            out.append(f)
            continue
        # 同一个文件里导出多个组件（TextbookVideo.tsx 导出 TextbookCoverStill）
        body = f.read_text(encoding="utf-8")
        if re.search(r"export const \w*Cover\w*", body) and f.stem in root_src:
            out.append(f)
    return out


def main() -> int:
    if not FRAME.exists():
        print(f"❌ 统一常量文件不存在: {FRAME}")
        return 1
    frame_src = FRAME.read_text(encoding="utf-8")

    bad = {k: num(frame_src, k) for k, v in EXPECT.items() if num(frame_src, k) != v}
    check("① coverFrame.ts 常量 = 规范值", not bad, "全部一致" if not bad else f"偏差 {bad}")

    comps = cover_components()
    tech = SRC / "tech" / "TechCoverCard.tsx"
    check("② TechCoverCard 复用统一常量（不抄数字）",
          tech.exists() and "from './coverFrame'" in tech.read_text(encoding="utf-8"),
          f"扫描封面组件 {len(comps)} 个")

    hard = []
    for f in comps:
        body = f.read_text(encoding="utf-8")
        if "LEGACY-COVER" in body:
            continue
        if "coverFrame" not in body:
            hard.append(f.name)
    check("③ 新封面组件必须复用 coverFrame（或标 LEGACY-COVER）", not hard,
          "全部合规" if not hard else f"未接入: {hard}")

    legacy = [f.name for f in comps if "LEGACY-COVER" in f.read_text(encoding="utf-8")]
    check("④ 旧版式已显式认账", True, f"LEGACY: {legacy or '无'}")

    try:
        sys.path.insert(0, str(PROJ))
        from src.pipeline.cover_compose import pick_clean_source  # noqa: E402
        src, rel = pick_clean_source(
            {"assets": {"bg": "assets/tech/video/bg/bg_circuit_v1_loop.mp4"}, "scenes": [{}]})
        check("⑤ 底图优先无字素材/背景（成片截帧仅兜底）", src is not None, rel)
    except Exception as exc:  # noqa: BLE001
        check("⑤ 底图优先无字素材/背景", False, str(exc)[:50])

    print(f"\nCOVER STANDARD {'OK' if not fails else 'FAIL'}"
          f"（{len(comps)} 个封面组件，{len(legacy)} 个 LEGACY）")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
