#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号补短标题回归（SPEC-20261115 的 R3/R4 本地可测部分）

面板唤起的真机路径（Vue `enterEditPost`）没法用单测覆盖，见 channels_edit 顶部注释。
本用例守的是取词规则与失败码契约：
  1. 短标题只在语义边界切，绝不半截词、绝不省略号、≤16 字；
  2. 人工指定优先；
  3. 失败码常量齐全（小品按 R4 表格验收）。
"""
from __future__ import annotations

import pathlib
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def test_derive() -> None:
    from src.utils.text_sanitizer import derive_short_title, pick_short_title

    cases = [
        ("英伟达董事会突然开会，讨论的居然是要不要换掉黄仁勋", "英伟达董事会突然开会"),
        ("英伟达开源爆款Auto-Ontology震撼发布", None),      # 不许出现 Auto-Onto 这种半截
        ("Reddit社区r/LocalLLaMA热度登顶", None),
    ]
    for head, expect in cases:
        got = derive_short_title(head, 16)
        check(f"① 派生不超 16 字: {head[:12]}", len(got) <= 16, f"got={got!r}")
        check(f"① 无省略号: {head[:12]}", "…" not in got and "..." not in got, f"got={got!r}")
        check(f"① 无半截英文词: {head[:12]}", not got.endswith(("Auto-Onto", "LocalL", "Onto")), f"got={got!r}")
        if expect:
            check(f"① 语义边界命中: {head[:12]}", got == expect, f"got={got!r}")

    check("① 人工指定优先", pick_short_title("随便什么描述", 16, "我定的标题") == "我定的标题")
    long_title = pick_short_title("英伟达董事会突然开会，讨论的居然是要不要换掉黄仁勋", 16)
    check("① 含逗号候选被拒（改用短语）", "，" not in long_title, f"got={long_title!r}")


def test_codes() -> None:
    from src.providers import channels_edit as CE

    for name in ("POST_NOT_FOUND", "MODIFY_PANEL_UNAVAILABLE", "SHORT_TITLE_INPUT_NOT_FOUND",
                 "SHORT_TITLE_NOT_LANDED", "SHORT_TITLE_UNRESOLVED", "ALREADY_SET",
                 "DESCRIPTION_CHANGED", "CDP_UNAVAILABLE"):
        check(f"③ 失败码存在 {name}", hasattr(CE, name))

    src = (PROJ / "src" / "providers" / "channels_edit.py").read_text(encoding="utf-8")
    check("② 只用 Vue enterEditPost 进编辑态", "enterEditPost(vm.post)" in src)
    check("② 不用只读信息卡 handleShowEditInfoPost", "handleShowEditInfoPost(vm.post)" not in src)
    check("② 短标题输入框按 placeholder 定位", 'input[placeholder*="短标题"]' in src)
    check("② 保存后回读比对描述（R2）", "DESCRIPTION_CHANGED" in src and "hit.description" in src)

    py = PROJ / "scripts" / "publish_channels.py"
    body = py.read_text(encoding="utf-8")
    check("① CLI 有 --fix-short-title", '"--fix-short-title"' in body)
    check("① CLI 有 --all-missing", '"--all-missing"' in body)
    check("① 不带开关时行为不变（走原分支）", "if args.fix_short_title:" in body)


def main() -> int:
    test_derive()
    test_codes()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
