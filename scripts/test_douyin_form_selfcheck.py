#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布表单自证回归（2026-11-15 17:57 空标题事故）

事故：同一条 CDP 通道 14:09、16:07 两次标题完整，17:57 作品数 44→45 但「无作品描述」、
空标题，回读 8 次全 miss → 说明「填标题」本身不稳定，不是 provider 专属。

本用例钉死：
  1. 点发布前的 assert_form_ready 必须读回标题+正文；被重渲染清空时要能就地补填；
  2. 补填仍不通过 → False（fail-closed），调用方不许点发布；
  3. 两条通道（provider / CLI）都接了这个断言，且不许再用 innerHTML/execCommand；
  4. 补标题脚本默认 dry-run。
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


class _Editor:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def click(self):
        pass

    def fill(self, text):
        self.page.caption = text

    def inner_text(self):
        return self.page.caption


class _Input:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def click(self):
        pass

    def fill(self, text):
        self.page.title = text

    def input_value(self):
        return self.page.title


class _KB:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if self.page.typing_target == "title" and key == "Delete":
            self.page.title = ""

    def insert_text(self, text):
        self.page.caption = text


class _Page:
    """可模拟「React 重渲染把标题清空」的假页面。"""

    def __init__(self, clear_title_after_fill=0):
        self.title = ""
        self.caption = ""
        self.clear_after = clear_title_after_fill
        self.fill_count = 0
        self.typing_target = "title"
        self.keyboard = _KB(self)

    def locator(self, sel):
        if "contenteditable" in sel:
            return _Editor(self)
        return _Input(self)


def test_assert_form_ready() -> None:
    from src.providers import douyin_form as F

    # 正常：标题+正文都在 → 通过
    p = _Page()
    p.title = "本地AI省账单？云端派表示不服"
    p.caption = "这一期聊聊端侧推理到底省了多少钱  #AI"
    ok, detail = F.assert_form_ready(p, "本地AI省账单？云端派表示不服", "这一期聊聊端侧推理到底省了多少钱")
    check("① 标题+正文齐全 → 放行", ok, f"title={detail['title_got']!r}")

    # 标题被清空，但还能补填 → 补填后放行
    p2 = _Page()
    p2.caption = "这一期聊聊端侧推理到底省了多少钱"
    ok2, d2 = F.assert_form_ready(p2, "本地AI省账单？云端派表示不服", "这一期聊聊端侧推理到底省了多少钱")
    check("① 标题被清空 → 就地补填后放行", ok2, f"filled={d2['title_got']!r}")

    # 标题补不进去（填空 → 一直为空）→ fail-closed
    class _Dead(_Page):
        def locator(self, sel):
            if "contenteditable" in sel:
                return _Editor(self)
            return _DeadInput(self)

    class _DeadInput(_Input):
        def fill(self, text):
            self.page.fill_count += 1
            self.page.title = ""      # 永远填不进去

    p3 = _Dead()
    p3.caption = "正文头部在这里随便写点字"
    ok3, d3 = F.assert_form_ready(p3, "本地AI省账单？云端派表示不服", "正文头部在这里随便写点字")
    check("② 标题怎么都填不进 → fail-closed", not ok3 and d3["title_ok"] is False, str(d3["title_got"]))

    # 正文没落上、且补不进去 → 也要拦
    class _DeadEditor(_Editor):
        def fill(self, text):
            pass                     # 补填也无效

        def inner_text(self):
            return ""                # 正文始终读不到

    class _DeadCaption(_Page):
        def __init__(self):
            super().__init__()
            self.keyboard.insert_text = lambda t: None   # 插入无效

        def locator(self, sel):
            if "contenteditable" in sel:
                return _DeadEditor(self)
            return _Input(self)

    p4 = _DeadCaption()
    p4.title = "本地AI省账单？云端派表示不服"
    p4.caption = ""
    ok4, d4 = F.assert_form_ready(p4, "本地AI省账单？云端派表示不服", "这一期聊聊端侧推理到底省了多少钱")
    check("② 正文缺失 → fail-closed", not ok4 and d4["caption_ok"] is False, str(d4["caption_ok"]))


def test_two_channels_wired() -> None:
    prov = (PROJ / "src" / "providers" / "douyin.py").read_text(encoding="utf-8")
    cli = (PROJ / "scripts" / "publish_douyin_cdp.py").read_text(encoding="utf-8")

    check("③ provider 接了提交前自证", "douyin_form.assert_form_ready(page, title, content_text)" in prov)
    check("③ CLI 接了提交前自证", "douyin_form.assert_form_ready(page, title, caption)" in cli)
    check("③ CLI 会填标题框", "douyin_form.fill_title(page, title)" in cli)
    check("③ provider 标题逻辑委托共用实现", "return douyin_form.fill_title(page, title)" in prov)

    for name, src in (("provider", prov), ("CLI", cli)):
        bad = "el.innerHTML = ''" in src or "document.execCommand('insertText'" in src
        check(f"③ {name} 不再用 innerHTML/execCommand 写正文", not bad)

    fix = PROJ / "scripts" / "fix_douyin_title.py"
    check("④ 补标题脚本存在", fix.exists())
    if fix.exists():
        s = fix.read_text(encoding="utf-8")
        check("④ 补标题脚本默认 dry-run", '"--apply", action="store_true"' in s.replace("--apply", "--apply"))


def main() -> int:
    test_assert_form_ready()
    test_two_channels_wired()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
