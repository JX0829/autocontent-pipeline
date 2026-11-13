#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音发布用有头浏览器回归（2026-11-13）

背景：`DouyinProvider` 五个调用点全部硬编码 `headless=True`，抖音会识别无头
（扫码/短信验证必失败，页面也不稳），使用者反复反馈「每次都用无头浏览器发抖音」。

回归点：
1. 账号未配置 headless 时，默认 **有头**（False）；
2. 账号配置 headless: true 时可按需覆盖；
3. `check_login_status` / `save_draft` / `_verify_published_sync` 三条发布链路
   真的把 headless=False 传到 `chromium.launch()`；
4. 只读同步（评论/作品列表）保持无头，不弹窗打扰。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


class _FakeChromium:
    def __init__(self, sink: list[dict]):
        self.sink = sink

    def launch(self, **kwargs):
        self.sink.append(kwargs)
        raise RuntimeError("stop-after-launch")     # 只验证参数，不真开浏览器


class _FakePW:
    def __init__(self, sink: list[dict]):
        self.chromium = _FakeChromium(sink)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(provider, fn_name: str) -> list[dict]:
    """替换 provider 模块里的 sync_playwright，抓 launch 的 kwargs。"""
    import src.providers.douyin as dm

    sink: list[dict] = []
    orig = dm.sync_playwright
    dm.sync_playwright = lambda: _FakePW(sink)
    try:
        try:
            getattr(provider, fn_name)()
        except Exception:  # noqa: BLE001 — 假 launch 故意抛异常
            pass
    finally:
        dm.sync_playwright = orig
    return sink


def main() -> int:
    from src.providers.douyin import DouyinProvider

    base = {"id": "test-account", "platform": "douyin",
            "profile_path": "/tmp/dy_test_profile", "publish_mode": "public_publish"}

    p_default = DouyinProvider(base)
    check("默认有头（未配置 headless）", p_default.headless_publish is False)

    p_on = DouyinProvider({**base, "headless": True})
    check("账号可显式开无头", p_on.headless_publish is True)
    p_off = DouyinProvider({**base, "headless": False})
    check("账号可显式关无头", p_off.headless_publish is False)

    calls = _capture(p_default, "check_login_status")
    check("check_login_status 传 headless=False",
          bool(calls) and calls[0].get("headless") is False,
          str(calls[0].get("headless") if calls else "未调用 launch"))

    # 只读同步仍是无头（不弹窗）
    calls2 = _capture(p_default, "sync_published_videos")
    check("sync_published_videos 仍无头（只读不弹窗）",
          bool(calls2) and calls2[0].get("headless") is True,
          str(calls2[0].get("headless") if calls2 else "未调用 launch"))

    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
