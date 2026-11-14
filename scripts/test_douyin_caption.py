#!/usr/bin/env python3
"""抖音简介（作品描述）排版回归 —— 离线，不碰平台。

守的三件事（对应用户实测的乱码截图）：
1. 旧写法逐字输入会被编辑器压平 + 插入零宽/NBSP，校验器必须判不合格；
2. 三段式写入（标题 / 简介 / 话题）必须判合格；
3. 写入策略要能降级，且**降级后仍不合格时必须报问题**，不许静默当成功。

用法：python3 scripts/test_douyin_caption.py [-v]
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.utils.douyin_caption import (  # noqa: E402
    build_caption,
    describe,
    insert_caption,
    verify_caption,
)

RESULTS: list[tuple[str, bool, str]] = []
VERBOSE = "-v" in sys.argv

PLATFORM_ACTUAL = (
    "黄仁勋辞任 英伟达突然换帅 \u200b\xa0#英伟达\xa0 #黄仁勋 \u200b\xa0#AI算力\xa0 "
    "\u200b\xa0#科技巨头\xa0 \u200b\xa0#行业人事\xa0 英伟达官方公告确认，黄仁勋辞去总裁、"
    "首席执行官和董事职务，即时生效，33年的任期到这里结束。"
)

TITLE = "黄仁勋辞任 英伟达突然换帅"
DESC = "英伟达官方公告确认，黄仁勋辞去总裁、首席执行官和董事职务，即时生效。"
TAGS = ["英伟达", "#黄仁勋", "AI算力"]


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    if VERBOSE or not cond:
        print(f"{'PASS' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


# ── 假编辑器 / 假页面：只实现 insert_caption 用到的口子 ─────────
class FakeEditor:
    """modes: {strategy: 回读文本工厂} —— 用来模拟各策略在真编辑器里的效果。"""

    def __init__(self, behaviors: dict):
        self.behaviors = behaviors
        self.text = ""
        self.calls: list[str] = []

    def click(self):
        self.calls.append("click")

    def fill(self, value: str):
        self.calls.append(f"fill({value!r})")
        self.text = self.behaviors.get("fill", value)

    def evaluate(self, _js, value):
        self.calls.append("evaluate")
        if self.behaviors.get("evaluate") is None:
            raise RuntimeError("evaluate 不可用")
        self.text = self.behaviors["evaluate"](value)

    def inner_text(self):
        return self.text


class FakeKeyboard:
    def __init__(self, editor: FakeEditor):
        self.editor = editor

    def press(self, key: str):
        self.editor.calls.append(f"press({key})")
        if self.editor.behaviors.get("press") is None:
            raise RuntimeError("press 不可用")
        self.editor.text += "\n" if key == "Enter" else key

    def insert_text(self, value: str):
        self.editor.calls.append("insert_text")
        factory = self.editor.behaviors.get("insert_text")
        if factory is None:
            raise RuntimeError("insert_text 不可用")
        # append=True 时模拟「多次 insert_text 累积」（原生造段策略）
        if self.editor.behaviors.get("append"):
            self.editor.text += factory(value)
        else:
            self.editor.text = factory(value)


class FakePage:
    def __init__(self, editor: FakeEditor):
        self.keyboard = FakeKeyboard(editor)


def test_build() -> None:
    cap = build_caption(TITLE, DESC, TAGS)
    check("三段式：三段之间各一个空行", cap.count("\n\n") == 2, repr(cap[:40]))
    check("三段式：话题在最后一段且补齐 #", cap.split("\n\n")[-1] == "#英伟达 #黄仁勋 #AI算力",
          repr(cap.split("\n\n")[-1]))
    check("空 tags 只出两段", build_caption(TITLE, DESC, []).count("\n\n") == 1)
    check("空 body 只出两段", build_caption(TITLE, "", TAGS).count("\n\n") == 1)


def test_verify() -> None:
    cap = build_caption(TITLE, DESC, TAGS)

    bad = verify_caption(PLATFORM_ACTUAL, cap)
    check("平台真实乱文本 → 不合格", bad["ok"] is False)
    check("乱文本能报出「换行不足」", any("换行" in p for p in bad["problems"]), str(bad["problems"]))
    check("乱文本能报出「零宽」", bad["zw"] > 0, f"zw={bad['zw']}")
    check("乱文本能报出「NBSP」", bad["nbsp"] > 0, f"nbsp={bad['nbsp']}")

    good = verify_caption(cap, cap)
    check("三段式回读 → 合格", good["ok"] is True, str(good))
    check("合格摘要可读", describe(good).startswith("ok"), describe(good))

    flat = cap.replace("\n\n", " ")
    check("反向：换行被吞 → 不合格", verify_caption(flat, cap)["ok"] is False)

    dirty = cap.replace("#英伟达", "\u200b\xa0#英伟达\xa0")
    check("反向：夹带零宽/NBSP → 不合格", verify_caption(dirty, cap)["ok"] is False)

    check("反向：正文被改字 → 不合格", verify_caption(cap.replace("辞去", "卸任"), cap)["ok"] is False)


def test_insert() -> None:
    cap = build_caption(TITLE, DESC, TAGS)

    # ① 原生造段正常（首选策略）
    ed = FakeEditor({"insert_text": lambda v: v, "press": True, "append": True})
    r = insert_caption(FakePage(ed), ed, cap)
    check("原生造段成功即采用", r["ok"] and r["strategy"] == "native_paragraphs", str(r["strategy"]))
    check("原生造段用了 Enter×2 而不是逐字",
          ed.calls.count("press(Enter)") == 4 and "insert_text" in ed.calls, str(ed.calls[:8]))

    # ② 原生造段被压平 → 整段 insert_text 成功
    ed = FakeEditor({"insert_text": lambda v: v.replace("\n\n", " "), "press": True,
                     "evaluate": lambda v: v})
    r = insert_caption(FakePage(ed), ed, cap)
    check("原生造段压平时用 html_div 兜底", r["ok"] and r["strategy"] == "html_div",
          f"{r['strategy']} problems={r['verify']['problems']}")

    # ③ 原生不可用 → DOM 兜底仍能写进去
    ed = FakeEditor({"evaluate": lambda v: v})
    r = insert_caption(FakePage(ed), ed, cap)
    check("原生不可用时 DOM 兜底", r["ok"] and r["strategy"] == "html_div", r["strategy"])

    # ④ 所有策略都被压平 → 必须报不合格（不静默）
    ed = FakeEditor({"evaluate": lambda v: v.replace("\n\n", " "),
                     "insert_text": lambda v: v.replace("\n\n", " "),
                     "fill": lambda v: v.replace("\n\n", " ")})
    r = insert_caption(FakePage(ed), ed, cap)
    check("全策略压平 → 判不合格", r["ok"] is False, str(r["strategy"]))
    check("不合格原因非空（可用于回执告警）", bool(r["verify"]["problems"]), str(r["verify"]["problems"]))


def test_video_desc_consistent() -> None:
    from src.utils.video_desc import build_desc
    draft = {"title": TITLE, "video_desc": DESC, "tags": TAGS}
    got = build_desc(draft, platform="douyin")
    check("video_desc(抖音) 与发布器构造一致", got == build_caption(TITLE, DESC, TAGS), repr(got[:50]))


def main() -> int:
    for fn in (test_build, test_verify, test_insert, test_video_desc_consistent):
        try:
            fn()
        except Exception:
            import traceback
            check(f"{fn.__name__} 未抛异常", False, traceback.format_exc(limit=2).replace("\n", " | "))
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, d) for l, c, d in RESULTS if not c]
    print(f"\nDOUYIN CAPTION {ok} PASS / {len(bad)} FAIL")
    for l, d in bad:
        print(f"  ✗ {l} {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
