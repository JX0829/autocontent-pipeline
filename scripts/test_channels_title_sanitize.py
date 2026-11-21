#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号标题归一化契约（2026-11-20 真机事故）。

事故：`aion-instruct-1` 发视频号时标题「微软把2.3B模型塞进Windows」被后台拒绝，
页面提示「标题包含特殊字符，符号仅支持书名号、引号、冒号、加号、问号、百分号、摄氏度，
逗号可用空格代替」→ 发布失败、post_list 回读必然不命中。

判据：
1. 数字之间的小数点（2.3B）→ 「点」，且**只**改小数点
2. 合法符号（书名号/引号/冒号/加号/问号/百分号/摄氏度/顿号逗号句号）原样保留
3. 其余符号（/ - & 等）→ 空格；连续空白压成一个
4. 纯中文/英文/数字标题不改（changed=False，不许误伤）

跑法：python3 scripts/test_channels_title_sanitize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.providers.channels_edit import sanitize_title  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def main() -> int:
    cases = [
        # (输入, 期望输出, 期望 changed)
        ("微软把2.3B模型塞进Windows", "微软把2点3B模型塞进Windows", True),
        ("英伟达营收翻倍，只字未提他", "英伟达营收翻倍，只字未提他", False),
        ("OpenAI「出海」：先签政府?", "OpenAI「出海」：先签政府?", False),
        ("《三体》+ 2.5% 提升：真的？", "《三体》+ 2点5% 提升：真的？", True),
        ("A/B测试-第2版", "A B测试 第2版", True),
        ("", "", False),
    ]
    for src, want, want_changed in cases:
        got, changed = sanitize_title(src)
        check(f"{src!r} → {want!r}", got == want, f"实际 {got!r}")
        check(f"{src!r} changed={want_changed}", changed is want_changed, f"实际 {changed}")

    # 关键负向：纯中文标题不许被改动（避免误伤绝大多数物料）
    clean_cn = "英伟达动作模型跑到15000帧"
    got, changed = sanitize_title(clean_cn)
    check("纯中文+数字标题不被改动", got == clean_cn and changed is False, f"{got!r} {changed}")

    print(f"\nCHANNELS TITLE {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
