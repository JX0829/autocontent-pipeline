#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 CDP 9333 已登录 Chrome 的抖音登录态同步到 provider 的 storage_state.json。

为什么：流水线 douyin provider 自己 launch 一个有头/无头 Chrome，读
`data/browser_profiles/douyin-tech/storage_state.json`；该快照过期时 publish 会
报 LOGIN_REQUIRED。9333 那个 Chrome 是人工维护的登录态，直接导出即可，不必再扫码。

安全约束（凭据文件，按不可逆写入处理）：
  · 默认 **dry-run**，只有 `--apply` 才落盘；
  · 源 Chrome 没有 sessionid/sessionid_ss 时**拒绝覆盖**（否则把过期态再写一遍）；
  · 落盘前备份旧文件；落盘后 chmod 600（凭据不能 world-readable）。

用法:
    python3 scripts/sync_douyin_storage.py            # 只检查
    python3 scripts/sync_douyin_storage.py --apply    # 真写
退出码：0 成功 / 3 源无登录态拒绝写 / 4 连不上 CDP
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
TARGET = PROJ / "data" / "browser_profiles" / "douyin-tech" / "storage_state.json"
CDP_URL = os.getenv("DOUYIN_CDP_URL", "http://127.0.0.1:9333")
DOMAINS = ("douyin.com", "douyinpic.com", "douyinstatic.com", "douyinvod.com", "bytedance.com")
REQUIRED_ANY = ("sessionid", "sessionid_ss")


def main() -> int:
    apply = "--apply" in sys.argv
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        print(f"FATAL: 缺 playwright（{exc}）；用项目解释器运行 "
              f"/opt/homebrew/opt/python@3.14/bin/python3.14")
        return 2

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=8000)
            ctx = browser.contexts[0] if browser.contexts else None
            if ctx is None:
                print(f"FATAL: {CDP_URL} 上没有可用的 browser context")
                return 4
            state = ctx.storage_state()
    except Exception as exc:  # noqa: BLE001 — 常驻 Chrome 没开就是环境问题
        print(f"FATAL: 连不上常驻 Chrome {CDP_URL}：{type(exc).__name__}: {exc}")
        print("       先把抖音 Chrome（CDP 9333）开起来并确认已登录")
        return 4

    cookies = [c for c in state.get("cookies", [])
               if any(d in (c.get("domain") or "") for d in DOMAINS)]
    origins = [o for o in state.get("origins", [])
               if any(d in (o.get("origin") or "") for d in DOMAINS)]
    names = sorted({c.get("name") for c in cookies})

    print(f"导出 cookie {len(cookies)} 条 / origin {len(origins)} 个")
    print("关键 cookie:", [n for n in names if n in REQUIRED_ANY] or "（无）")

    if not any(n in names for n in REQUIRED_ANY):
        print("FATAL: 源 Chrome 没有 sessionid —— 拒绝覆盖（否则会把过期态再写一遍）")
        return 3

    if not apply:
        print(f"[DRY-RUN] 未写。目标：{TARGET}")
        print("         确认无误后加 --apply 落盘")
        return 0

    if TARGET.exists():
        bak = TARGET.with_name(f"storage_state.json.bak.{int(time.time())}")
        shutil.copy2(TARGET, bak)
        print(f"[backup] {bak.name}")

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps({"cookies": cookies, "origins": origins},
                                 ensure_ascii=False), encoding="utf-8")
    os.chmod(TARGET, 0o600)          # 凭据文件不 world-readable
    print(f"[write] {TARGET} ({TARGET.stat().st_size} bytes, mode 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
