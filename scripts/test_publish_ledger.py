#!/usr/bin/env python3
"""发布台账（publish_ledger）语义回归

起因：2026-11-11 同一条视频在 B站被发了两遍（07:49 / 07:56）。
本测试锁死"什么情况允许重发、什么情况必须拦住"的判定，防止有人把 unverified
并回 failed（那样会重新打开重复发布的口子）。

核心断言：
  · 已 published → 拦
  · 未收尾 claimed → 拦
  · 结果未知 unverified → **拦**（这条最容易被改错）
  · 只有确定没发出去（LOGIN_EXPIRED / 校验失败等）才 failed → 可重试
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.publish_ledger import (PublishBlocked, check_and_claim, classify_failure,  # noqa: E402
                               list_rows, mark_deleted, mark_published, mark_unverified,
                               record_outcome, release)

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def blocked(fn) -> bool:
    try:
        fn()
    except PublishBlocked:
        return True
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pledger_"))
    db = tmp / "state.db"
    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video-bytes-for-sha")

    print("=== 发布台账语义回归 ===")

    print("\n[1] 首次占用")
    key, info = check_and_claim("bilibili", "bili-main", video, "标题A", path=db)
    check("首次可以发布", bool(key) and info["platform"] == "bilibili")
    check("第二次同视频被拦（未收尾 claimed）",
          blocked(lambda: check_and_claim("bilibili", "bili-main", video, "标题A", path=db)))

    print("\n[2] 已发布")
    mark_published(key, "BV1xx411c7mD", path=db)
    check("published 后仍被拦",
          blocked(lambda: check_and_claim("bilibili", "bili-main", video, "改个标题也不行", path=db)))
    check("published 不能被 release 释放（防止误清）", release(key, path=db) is False)

    print("\n[3] 失败分类")
    check("LOGIN_EXPIRED → failed（可重试）", classify_failure("LOGIN_EXPIRED") == "failed")
    check("ORIGINAL_CHECKBOX_NOT_FOUND → failed", classify_failure("ORIGINAL_CHECKBOX_NOT_FOUND") == "failed")
    check("PUBLISHED_UNVERIFIED → unverified（禁重发）",
          classify_failure("PUBLISHED_UNVERIFIED") == "unverified")
    check("未知异常 → unverified（禁重发）", classify_failure("", "TimeoutError: 提交后超时") == "unverified")

    print("\n[4] unverified 必须拦住重发")
    video2 = tmp / "video2.mp4"
    video2.write_bytes(b"another-video")
    k2, _ = check_and_claim("channels", "ch-main", video2, "标题B", path=db)
    state = record_outcome(k2, False, status="PUBLISHED_UNVERIFIED", reason="点了发表但没看到结果", path=db)
    check("record_outcome 落库为 unverified", state == "unverified")
    check("unverified 后禁止重发",
          blocked(lambda: check_and_claim("channels", "ch-main", video2, "标题B", path=db)))
    check("release 可以清掉 unverified（人工核对后）", release(k2, path=db) is True)
    k2b, _ = check_and_claim("channels", "ch-main", video2, "标题B", path=db)
    check("清理后可重新占用", bool(k2b))

    print("\n[5] failed 可重试")
    video3 = tmp / "video3.mp4"
    video3.write_bytes(b"third-video")
    k3, _ = check_and_claim("douyin", "dy-main", video3, "标题C", path=db)
    st = record_outcome(k3, False, status="LOGIN_EXPIRED", reason="cookie 过期", path=db)
    check("确定没发出去 → failed", st == "failed")
    k3b, _ = check_and_claim("douyin", "dy-main", video3, "标题C", path=db)
    check("failed 后允许重试", bool(k3b))

    print("\n[6] 隔离性")
    check("换账号不影响",
          bool(check_and_claim("douyin", "dy-other", video3, "标题C", path=db)[0]))
    check("换平台不影响",
          bool(check_and_claim("bilibili", "dy-main", video3, "标题C", path=db)[0]))

    print("\n[7] 人工删除后允许重发")
    mark_deleted(k3b, path=db)
    check("deleted 后可重新占用",
          bool(check_and_claim("douyin", "dy-main", video3, "标题C", path=db)[0]))

    print("\n[8] 台账可列出")
    rows = list_rows(path=db)
    check("list_rows 能读出台账", len(rows) >= 4, str(len(rows)))
    check("台账带 status 字段", all("status" in r for r in rows))

    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("PUBLISH_LEDGER CONTRACT FAIL")
        return 1
    print("PUBLISH_LEDGER CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
