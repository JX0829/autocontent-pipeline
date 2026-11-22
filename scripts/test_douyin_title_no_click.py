#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音标题框 30s 卡点回归（2026-11-20 P0）。

真机实证：输入框存在且可见、elementFromPoint 命中 INPUT 自己（没被遮挡），
但 click() 3s/30s 一律超时 —— Playwright 的可点击判据在持续动效页永远不满足。
修法：不点，直接 fill()；失败退 JS 原生 setter + input 事件。
"""
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.providers import douyin_form as df  # noqa: E402

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


class TitleLocator:
    """click() 恒超时（复现线上），fill() 可用。"""

    def __init__(self, *, fill_works: bool = True):
        self.value = ""
        self.click_calls = 0
        self.fill_calls = 0
        self.fill_works = fill_works

    def count(self):
        return 1

    def is_visible(self):
        return True

    def click(self, timeout=None, force=False):
        self.click_calls += 1
        raise TimeoutError("Locator.click: Timeout 30000ms exceeded.")

    def fill(self, val, timeout=None):
        self.fill_calls += 1
        if not self.fill_works:
            raise TimeoutError("fill timeout")
        self.value = val

    def input_value(self):
        return self.value


class FakePage:
    def __init__(self, loc):
        self._loc = loc
        self.js_sets = 0

    def locator(self, sel):
        class _L:
            def __init__(self, loc):
                self.first = loc
        return _L(self._loc)

    def evaluate(self, js, arg=None):
        if "setter.call" in js:
            self.js_sets += 1
            self._loc.value = (arg or {}).get("val", "")
            return True
        if "cands" in js:
            return {"cands": [], "topTag": "INPUT", "modals": 3}
        return 0

    def wait_for_timeout(self, ms):
        return None


def main() -> int:
    # ① 正常：fill 可用 → 成功且**一次都没点**
    loc = TitleLocator()
    ok, landed, err = df.fill_title(FakePage(loc), "英伟达新动作模型")
    check("① fill 路径成功", ok, f"landed={landed!r} err={err[:40]!r}")
    check("① 完全不调用 click（30s 卡点根源）", loc.click_calls == 0, f"click={loc.click_calls}")

    # ② fill 也不行 → JS setter 兜底
    loc2 = TitleLocator(fill_works=False)
    pg2 = FakePage(loc2)
    ok2, landed2, err2 = df.fill_title(pg2, "谷歌 Genie 4 开放")
    check("② fill 失败退 JS setter 成功", ok2, f"landed={landed2!r}")
    check("② 确实走了 JS 写入", pg2.js_sets >= 1, f"js_sets={pg2.js_sets}")
    check("② 仍然没点 click", loc2.click_calls == 0, f"click={loc2.click_calls}")

    # ③ 空标题 → 直接失败，不尝试输入
    ok3, _, err3 = df.fill_title(FakePage(TitleLocator()), "   ")
    check("③ 空标题拒绝", not ok3 and "空" in err3, err3)

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
