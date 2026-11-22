#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_privacy 回归（2026-11-19 P0：public_publish 卡在已选中的「公开可见」硬点 30s）。

用假 page/locator 覆盖三条路径：
  ① 已经是 checked=true → 必须直接成功且**一次都不点**（这就是 30s 超时的根因）
  ② 未选中 → JS input.click() 后回读 checked → 成功
  ③ 点了也不生效 → 失败并带 checked 状态（不静默、不假绿）
"""
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.providers.douyin import select_privacy  # noqa: E402

PASS = 0
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}" + (f" | {extra}" if extra else ""))
    else:
        FAIL.append(name)
        print(f"FAIL  {name}" + (f" | {extra}" if extra else ""))


class FakeLocator:
    def __init__(self, checked: bool, *, reacts: bool = True, unknown: bool = False):
        self.checked = checked
        self.reacts = reacts
        self.unknown = unknown          # 读不到真实控件状态（既没 input 也没 aria-checked）
        self.click_calls = 0
        self.js_clicks = 0

    def count(self) -> int:
        return 1

    def evaluate(self, js: str):
        if "el.querySelector" in js and "return !!inp.checked" in js:
            return None if self.unknown else self.checked
        if ".click()" in js:            # ② JS 点击
            self.js_clicks += 1
            if self.reacts:
                self.checked = True
            return None
        return None

    def click(self, timeout=None, force=False) -> None:
        self.click_calls += 1
        if self.reacts:
            self.checked = True


class FakePage:
    def __init__(self, loc: FakeLocator):
        self._loc = loc

    def locator(self, sel: str):
        class _L:
            def __init__(self, loc):
                self.first = loc
        return _L(self._loc)

    def wait_for_timeout(self, ms: int) -> None:
        return None


def main() -> int:
    # ① 已选中
    loc = FakeLocator(True)
    ok, why = select_privacy(FakePage(loc), "public_publish")
    check("① 已选中直接成功", ok, f"why={why!r}")
    check("① 已选中不点击（根因）", loc.js_clicks == 0 and loc.click_calls == 0,
          f"js={loc.js_clicks} pw={loc.click_calls}")

    # ② 未选中但可被 JS 点中
    loc2 = FakeLocator(False)
    ok2, why2 = select_privacy(FakePage(loc2), "public_publish")
    check("② 未选中 JS 点中", ok2, f"why={why2!r}")
    check("② JS 点击一次", loc2.js_clicks == 1, f"js={loc2.js_clicks}")

    # ③ 点了也不生效
    loc3 = FakeLocator(False, reacts=False)
    ok3, why3 = select_privacy(FakePage(loc3), "public_publish")
    check("③ 点不动必须失败", not ok3, f"why={why3!r}")
    check("③ 失败原因带 checked", "checked" in (why3 or ""), why3)

    # ④ 未知 mode
    ok4, why4 = select_privacy(FakePage(FakeLocator(True)), "nope")
    check("④ 未知 mode 失败", not ok4 and "未知" in why4, why4)

    # ⑤ 读不到控件状态（没有 input 也没有 aria-checked）→ 必须 fail-closed，不许假绿
    loc5 = FakeLocator(False, reacts=False, unknown=True)
    ok5, why5 = select_privacy(FakePage(loc5), "public_publish")
    check("⑤ 状态不明必须失败（隐私项不许假绿）", not ok5, f"why={why5!r}")

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
