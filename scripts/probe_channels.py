#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""视频号**只读探针**：确认「能不能发」，绝不发。

存在的理由：发布入口已收口到流水线（`runner --publish channels`），但排障时需要
一个「不发布、只看状态」的手工动作。这个脚本刻意不含任何发布调用：
  · CDP 9334 是否在线、常驻实例是否被误拉成无头
  · 登录态是否真的有效（不只看端口）
  · 发布页控件是否就位（标题框 / 正文框 / 原创勾选 / 发表按钮）

用法：
    python3.14 scripts/probe_channels.py            # 全量探针
    python3.14 scripts/probe_channels.py --json     # 机器可读

退出码：0 全部就绪 / 1 有项不就绪（原因见输出） / 2 环境错误（依赖缺失等）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

PUBLISH_URL = "https://channels.weixin.qq.com/platform/post/create"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    from src.providers.channels_session import (
        CDP_URL, ChannelsSessionError, ensure, probe, running_headless, verify_login,
    )

    result: dict = {"cdp_url": CDP_URL, "checks": [], "ok": True}

    def add(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            result["ok"] = False

    # 1) CDP 端口
    ver = probe()
    add("cdp_online", bool(ver),
        (ver or {}).get("Browser", "") if ver else "端口无响应：先启动常驻 Chrome")

    # 2) 常驻实例必须是有头（无头会被风控/登录态校验打回）
    headless = running_headless()
    add("headless_is_false", headless is False,
        "无法判定（进程没起来）" if headless is None else f"running_headless={headless}")

    # 3) 真访问后台确认登录态
    try:
        cdp = ensure(headless=False)
        ok_login, url = asyncio.run(verify_login(cdp))
        add("login_active", ok_login, url)
    except ChannelsSessionError as exc:
        add("login_active", False, f"会话不可用：{exc}")
        ok_login, url = False, ""

    # 4) 发布页控件（不点发表）
    controls: dict = {}
    if ok_login:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0]
            page = ctx.new_page()
            try:
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(6000)
                controls = page.evaluate("""() => {
                    const out = {url: location.href, inputs: 0, editors: 0,
                                 original: false, publish_btn: false};
                    for (const f of document.querySelectorAll('iframe')) {
                      try {
                        const d = f.contentDocument;
                        if (!d) continue;
                        out.inputs += d.querySelectorAll('input[type=text], input:not([type])').length;
                        out.editors += d.querySelectorAll('div[contenteditable="true"]').length;
                        const t = d.body ? d.body.innerText : '';
                        if (t.includes('原创')) out.original = true;
                        if (/发表|发布/.test(t)) out.publish_btn = true;
                      } catch (e) { /* 跨域 iframe 正常跳过 */ }
                    }
                    return out;
                }""")
            except Exception as exc:  # noqa: BLE001 — 探针失败要看得见原因
                controls = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            finally:
                page.close()

    add("publish_page_reachable", bool(controls) and "error" not in controls,
        json.dumps(controls, ensure_ascii=False)[:220])
    result["controls"] = controls

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=== 视频号只读探针（不发布）===")
        for c in result["checks"]:
            print(f"  {'✅' if c['ok'] else '❌'} {c['check']:<22} {c['detail']}")
        print("PROBE_OK" if result["ok"] else "PROBE_FAIL —— 按上面 ❌ 修，再重跑")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
