#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""APPROVAL_TIMEOUT_SEC 生效性回归（cyber77-1，2026-11-13）

旧实现 `int(prof.get("timeout_sec") or os.getenv(...))`：pipeline.yaml 里写死的 600
永远优先，文档让传 1800 也没用，人不在场 10 分钟就静默 reject。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.approval.feishu import _resolve_timeout  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def main() -> int:
    prof = {"timeout_sec": 600}

    os.environ["APPROVAL_TIMEOUT_SEC"] = "1800"
    check("env 优先于 pipeline.yaml", _resolve_timeout(prof) == 1800,
          str(_resolve_timeout(prof)))

    os.environ["APPROVAL_TIMEOUT_SEC"] = ""
    check("env 为空回落到 pipeline.yaml", _resolve_timeout(prof) == 600)

    del os.environ["APPROVAL_TIMEOUT_SEC"]
    check("无 env 时用 pipeline.yaml", _resolve_timeout(prof) == 600)
    check("无 env 无 profile 时默认 600", _resolve_timeout({}) == 600)

    os.environ["APPROVAL_TIMEOUT_SEC"] = "abc"
    check("非法 env 不吞掉整轮，回落默认", _resolve_timeout(prof) == 600)
    os.environ["APPROVAL_TIMEOUT_SEC"] = "0"
    check("0/负数 env 被拒，回落默认", _resolve_timeout(prof) == 600)
    os.environ.pop("APPROVAL_TIMEOUT_SEC", None)

    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
