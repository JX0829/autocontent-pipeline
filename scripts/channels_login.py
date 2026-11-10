#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号扫码登录：有头 Chrome 打开助手后台，人工扫码后保存 storage_state

用法:
  python3 scripts/channels_login.py            # 打开浏览器，扫码登录
  python3 scripts/channels_login.py --check    # 只检查现有登录态是否有效

登录态保存到 data/browser_profiles/channels-tech/storage_state.json，
后续 publish_channels.py 直接复用。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PROFILE = PROJ / "data" / "browser_profiles" / "channels-tech"
STATE = PROFILE / "storage_state.json"
LOGIN_URL = "https://channels.weixin.qq.com/platform/post/create"


async def run(check_only: bool) -> int:
    from playwright.async_api import async_playwright

    PROFILE.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=check_only, channel="chrome",
                                          args=["--no-sandbox",
                                                "--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            storage_state=str(STATE) if STATE.exists() else None,
            viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)

        if check_only:
            ok = "login" not in page.url.lower()
            print(("✅ 登录态有效: " if ok else "❌ 登录态已过期: ") + page.url)
            await browser.close()
            return 0 if ok else 2

        print("浏览器已打开，请用微信扫码登录视频号助手…")
        print("登录成功（页面进入发布页）后本脚本会自动保存登录态。")
        for _ in range(180):                      # 最多等 6 分钟
            await asyncio.sleep(2)
            if "login" not in page.url.lower() and "platform" in page.url:
                await asyncio.sleep(3)
                await ctx.storage_state(path=str(STATE))
                print(f"✅ 登录态已保存: {STATE}")
                await browser.close()
                return 0
        print("❌ 等待登录超时")
        await browser.close()
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查现有登录态")
    args = ap.parse_args()
    return asyncio.run(run(args.check))


if __name__ == "__main__":
    sys.exit(main())
