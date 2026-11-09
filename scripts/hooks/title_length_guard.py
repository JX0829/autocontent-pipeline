#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.title_length_guard（gate, warn）—— 标题长度护栏。

平台硬上限不同（抖音 30 / B站 40），按 context.platform 取策略；
超限只 warn（`on_fail: warn`），不阻断出片，但必须留可观测记录。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

DEFAULT_MAX = 30
PLATFORM_MAX = {"douyin": 30, "bilibili": 40, "wechat": 30}


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or {}
    title = str(draft.get("title") or man.get("title") or "").strip()
    if not title:
        C.fail("title_length_guard: 没有标题可校验")

    platform = str(c.get("platform") or "").lower()
    limit = PLATFORM_MAX.get(platform, DEFAULT_MAX)
    n = len(title)
    if n > limit:
        C.fail(f"title_length_guard: 标题 {n} 字超平台上限 {limit}（{platform or 'default'}）",
               length=n, limit=limit)
    if n < 8:
        C.fail(f"title_length_guard: 标题仅 {n} 字，信息量过低", length=n, limit=limit)

    C.emit(True, patch={"title_length": n, "title_limit": limit})


if __name__ == "__main__":
    main()
