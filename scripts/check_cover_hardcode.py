#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面模板硬编码检测（T4.4-5 假数据检测）

背景：QA#7 的根因是横屏封面组件里写死了营销话术（副标题、右侧三卡文案），
数据一变，封面就出现"文案不是本条内容"的假数据。

判据：封面/竖版模板组件中**不允许出现中文/英文话术字面量**，文案必须全部来自 props。
允许的例外（白名单）：
  · 单位/符号类（如 "·"、":"、"/"、"B站" 之类固定标识需逐条登记）
  · className/style 中的非文案字符串（本脚本只扫 JSX 文本节点与字符串字面量中的 CJK）

用法：
  python3 scripts/check_cover_hardcode.py            # 扫描默认目标
  python3 scripts/check_cover_hardcode.py --all      # 扫描所有模板组件
退出码：0 = 干净；1 = 发现硬编码话术
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
COMPONENTS = PROJ / "renderer" / "src" / "components"

DEFAULT_TARGETS = ["BilibiliLandscapeCover.tsx", "BilibiliLandscapeVideo.tsx"]

# 白名单：与内容无关的固定 UI 装饰（栏目名、平台标识、底部引导语）。
# 判据是「它是否随本条内容变化」——不随内容变化的界面文字允许写死；
# 凡是随选题变化的话术（副标题、三卡正文、标签）必须来自 props。
WHITELIST = {
    "B站", "B站前沿科技速递", "🔥 B站前沿科技速递",   # 平台标识
    "◈ 核心看点", "◈ 关键机制", "◈ 实操提醒",        # 三卡固定栏目名
    "核心看点", "关键机制", "实操提醒",
    "💡 源码直链与提示词见下方简介与置顶评论",        # 底部固定引导
    "完整项目直链与启动命令见视频简介与置顶",
}

CJK = re.compile(r"[\u4e00-\u9fff]")
# 只匹配引号里的字符串与 JSX 文本节点
STR_LIT = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""")
JSX_TEXT = re.compile(r">\s*([^<>{}\n]*[\u4e00-\u9fff][^<>{}\n]*)\s*<")


def scan(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*", "#")):
            continue
        # 日志/告警文案不上屏，不算"封面假数据"（例如 console.warn('[cover] ... 缺失')）
        if any(k in stripped for k in ("console.", "logger.", "warn(", "error(")):
            continue
        for m in STR_LIT.finditer(line):
            val = m.group(2).strip()
            if not val or not CJK.search(val):
                continue
            if val in WHITELIST:
                continue
            # className/style/路径里的中文极少，但 data-* 与注释已排除
            hits.append((lineno, val))
        for m in JSX_TEXT.finditer(line):
            val = m.group(1).strip()
            if val and val not in WHITELIST:
                hits.append((lineno, val))
    return hits


def main() -> int:
    targets = DEFAULT_TARGETS
    if "--all" in sys.argv:
        targets = [p.name for p in sorted(COMPONENTS.glob("*.tsx"))]

    total = 0
    for name in targets:
        p = COMPONENTS / name
        if not p.exists():
            print(f"  ? 跳过（不存在）: {name}")
            continue
        hits = scan(p)
        if not hits:
            print(f"  ✅ {name}: 无硬编码话术")
            continue
        total += len(hits)
        print(f"  ❌ {name}: {len(hits)} 处硬编码")
        for lineno, val in hits:
            print(f"       L{lineno}: {val!r}")

    print()
    if total:
        print(f"T4.4 假数据检测 FAIL：{total} 处文案字面量（应全部来自 props）")
        return 1
    print("T4.4 假数据检测 PASS：封面文案全部来自 props")
    return 0


if __name__ == "__main__":
    sys.exit(main())
