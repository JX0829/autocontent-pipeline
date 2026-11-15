#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音标题/描述落位回归（2026-11-15 local-vs-cloud-1 事故）

事故：视频发出去了，平台上「无作品描述」；回读按标题找 → 永远不命中 →
台账 unverified + READBACK_NOT_FOUND，可作品其实已经在列表里。

本用例钉死四条纪律：
  1. 标题必须填进「作品标题」框并回读自证，落不上 → TITLE_NOT_LANDED，不许发布
  2. 正文用真实输入事件写入（insert_text），不许 innerHTML/execCommand 绕过 React
  3. 回读两级命中：标题 → title_ok；正文命中但标题空 → PUBLISHED_NO_TITLE（有作品无标题）
  4. 台账：TITLE_NOT_LANDED 可重试（failed）；PUBLISHED_NO_TITLE 记 unverified 但挂上 post_id
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


# ───────────────────────── 假页面：只实现 _fill_title 用到的口子 ─────────────────────────
class _Loc:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel

    @property
    def first(self):            # 模拟 Playwright 的 .first
        return self

    def count(self):
        return 1 if self.page.has_title_input else 0

    def is_visible(self):
        return True

    def click(self):
        pass

    def fill(self, text):
        self.page.fill_calls += 1
        # 前 N 次填不进去（模拟 SPA 把值吞掉）
        self.page.value = "" if self.page.fill_calls <= self.page.swallow else text

    def input_value(self):
        return self.page.value


class _KB:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key == "Delete":
            self.page.value = ""


class _TitlePage:
    def __init__(self, has_title_input=True, swallow=0):
        self.has_title_input = has_title_input
        self.swallow = swallow
        self.value = ""
        self.fill_calls = 0
        self.keyboard = _KB(self)

    def locator(self, sel):
        return _Loc(self, sel)

    @property
    def first(self):            # 模拟 Playwright 的 .first
        return self


def test_fill_title() -> None:
    from src.providers.douyin import DouyinProvider

    prov = DouyinProvider({"id": "t", "profile_path": "./data/browser_profiles/t"})

    ok, landed, err = prov._fill_title(_TitlePage(), "本地AI省账单？云端派表示不服")
    check("① 标题一次落位", ok and landed.startswith("本地AI省账单"), f"{ok} {landed[:20]}")

    ok2, landed2, err2 = prov._fill_title(_TitlePage(swallow=1), "本地AI省账单？云端派表示不服")
    check("① 第一轮被吞 → 重试后落位", bool(ok2 and landed2), f"rounds>1 fill_calls=2")

    ok3, _, err3 = prov._fill_title(_TitlePage(swallow=9), "标题")
    check("① 三轮都落不上 → 失败且给原因", not ok3 and "回读不一致" in err3, err3[:40])

    ok4, _, err4 = prov._fill_title(_TitlePage(has_title_input=False), "标题")
    check("① 找不到标题框 → 失败", not ok4 and "找不到" in err4, err4[:30])

    ok5, _, _ = prov._fill_title(_TitlePage(), "   ")
    check("① 空标题 → 失败（不发布）", not ok5)


def test_readback_two_level() -> None:
    from src.providers.douyin import DouyinProvider

    m = DouyinProvider._match_readback
    page_with_title = "作品 (37) 本地AI省账单？云端派表示不服 00:33"
    check("③ 标题命中 → 成功判据", m(page_with_title, "本地AI省账单？云端派", "正文头") == (True, True, "title"))

    page_no_title = "作品 (37) 无作品标题 00:33 这一期聊聊端侧推理到底省了多少钱"
    found, title_ok, matched = m(page_no_title, "本地AI省账单？云端派", "这一期聊聊端侧推理到底")
    check("③ 有作品无标题 → found 但 title_ok=False",
          (found, title_ok, matched) == (True, False, "caption"), f"{found},{title_ok},{matched}")

    check("③ 两边都没有 → 未命中", m("作品 (37) 无关内容", "标题A", "正文B") == (False, False, ""))


def test_desc_no_title_in_body() -> None:
    from src.utils.video_desc import build_desc

    draft = {"title": "本地AI省账单？云端派表示不服", "video_desc": "这里是正文内容", "tags": ["AI", "云"]}
    out = build_desc(draft, platform="douyin")
    check("②/① 抖音正文不再打头塞标题", "本地AI省账单" not in out, out[:40])
    check("② 抖音正文保留描述内容", "这里是正文内容" in out, out[:40])


def test_ledger_states() -> None:
    from src.publish_ledger import check_and_claim, classify_failure, record_outcome

    check("④ TITLE_NOT_LANDED 可重试（没点发布）",
          classify_failure("TITLE_NOT_LANDED", "") == "failed")
    check("④ CAPTION_MISMATCH 可重试", classify_failure("CAPTION_MISMATCH", "") == "failed")
    check("④ PUBLISHED_NO_TITLE 不可重试（作品已存在）",
          classify_failure("PUBLISHED_NO_TITLE", "有作品无标题") == "unverified")

    work = pathlib.Path(tempfile.mkdtemp(prefix="dytitle_"))
    db = work / "state.db"
    v = work / "a.mp4"
    v.write_bytes(b"a" * 16)
    key, _ = check_and_claim("douyin", "acct", str(v), "本地AI省账单", path=db)
    st = record_outcome(key, False, status="PUBLISHED_NO_TITLE",
                        external_id="readback_1794672416",
                        reason="作品已出现但标题未落上", path=db)
    check("④ 有作品无标题 → unverified", st == "unverified", st)

    import sqlite3
    row = sqlite3.connect(str(db)).execute(
        "select status, post_id from publish_ledger where publish_key=?", (key,)).fetchone()
    check("④ unverified 行必须挂上 post_id", row == ("unverified", "readback_1794672416"), str(row))


def test_source_discipline() -> None:
    """静态纪律：不许再用 innerHTML='' + execCommand 写正文（绕过 React，发布时被丢弃）。"""
    src = (PROJ / "src" / "providers" / "douyin.py").read_text(encoding="utf-8")
    form = (PROJ / "src" / "providers" / "douyin_form.py").read_text(encoding="utf-8")
    check("② 正文改用 keyboard.insert_text（单一实现 douyin_form）", "keyboard.insert_text" in form)
    bad = 'el.innerHTML = \'\'' in src + form or "document.execCommand('insertText'" in src + form
    check("② 不再用 innerHTML/execCommand 直改 DOM", not bad)


def main() -> int:
    test_fill_title()
    test_readback_two_level()
    test_desc_no_title_in_body()
    test_ledger_states()
    test_source_discipline()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
