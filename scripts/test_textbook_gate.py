#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课文模板默认禁用契约（用户口径 2026-11-22）

原话：「后续，除非强制要求使用课文背后故事，否则不能使用对应的模板和内容。」

判据：
  T1 课文物料没写 force_textbook → 抛 TEXTBOOK_REQUIRES_EXPLICIT
  T2 课文物料写了 force_textbook=true → 放行
  T3 ALLOW_TEXTBOOK=1 环境阀 → 放行（历史 run 重渲染用）
  T4 科技物料（is_literary=False）→ 不受影响
  T5 template_id / content_type / textbook 字段任一命中都算文学类（三条路径都拦）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.pipeline.executors import ExecutorError, assert_textbook_authorized  # noqa: E402

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ← {detail}" if detail and not ok else ""))


def expect_block(name: str, mat: dict, tpl: str = "textbook_story") -> None:
    try:
        assert_textbook_authorized(True, mat, tpl)
        check(name, False, "应当拦截但放行了")
    except ExecutorError as exc:
        check(name, "TEXTBOOK_REQUIRES_EXPLICIT" in str(exc), str(exc)[:80])


def main() -> int:
    os.environ.pop("ALLOW_TEXTBOOK", None)

    expect_block("T1 无 force_textbook → 拦截", {"title": "元日", "textbook": {"poem_title": "元日"}})
    expect_block("T5 content_type=ink_story → 拦截", {"content_type": "ink_story"})

    try:
        assert_textbook_authorized(True, {"title": "元日", "force_textbook": True}, "textbook_story")
        check("T2 force_textbook=true → 放行", True)
    except ExecutorError as exc:
        check("T2 force_textbook=true → 放行", False, str(exc)[:80])

    os.environ["ALLOW_TEXTBOOK"] = "1"
    try:
        assert_textbook_authorized(True, {"title": "元日"}, "textbook_story")
        check("T3 ALLOW_TEXTBOOK=1 → 放行", True)
    except ExecutorError as exc:
        check("T3 ALLOW_TEXTBOOK=1 → 放行", False, str(exc)[:80])
    finally:
        os.environ.pop("ALLOW_TEXTBOOK", None)

    try:
        assert_textbook_authorized(False, {"title": "DeepSeek 额度收紧"}, "tech_story")
        check("T4 科技物料不受影响", True)
    except ExecutorError as exc:
        check("T4 科技物料不受影响", False, str(exc)[:80])

    print(f"\n汇总: {len(PASS)}/{len(PASS) + len(FAIL)} PASS")
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
