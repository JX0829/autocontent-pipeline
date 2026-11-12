#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号登录：**有头 Chrome + 持久化 profile**（2026-11-12 改造）

为什么改：原实现每次都是「一次性浏览器 + storage_state 快照」，视频号会话约 24h 过期，
每天第一条必卡扫码。改成持久化 user-data-dir（同抖音 CDP 9333 思路）后：
  · cookie / localStorage / 指纹都留在磁盘 profile 里，登录态更耐久
  · 掉线时可唤起有头窗口人工再扫，扫完自动回写 storage_state 供发布脚本复用

用法:
  python3 scripts/channels_login.py               # 有头打开，需要时人工扫码
  python3 scripts/channels_login.py --check       # 只检查（无头，不开窗）
  python3 scripts/channels_login.py --qr-out /tmp/qr.png   # 顺便导出二维码
  python3 scripts/channels_login.py --timeout 300 # 自定义等待秒数

产物:
  data/browser_profiles/channels-tech/chrome_profile/   持久化 Chrome profile（长期）
  data/browser_profiles/channels-tech/storage_state.json 快照（供 publish_channels.py 复用）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
PROFILE = PROJ / "data" / "browser_profiles" / "channels-tech"
USER_DATA_DIR = PROFILE / "chrome_profile"
STATE = PROFILE / "storage_state.json"
HOME_URL = "https://channels.weixin.qq.com/platform"
LOGIN_URL = "https://channels.weixin.qq.com/platform/post/create"


def _find_qr_box(png_bytes: bytes) -> tuple[int, int, int, int] | None:
    """从整页截图里按二维码特征定位：深色比 0.30~0.62 且黑白跳变密集。

    不依赖 class 名（视频号改版就变），只看几何+纹理特征。
    """
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(png_bytes)).convert("L")
    w, h = im.size
    px = im.load()

    def score(x: int, y: int, s: int) -> tuple[float, int]:
        dark = trans = n = 0
        for yy in range(y, min(y + s, h), 3):
            prev = None
            for xx in range(x, min(x + s, w), 3):
                v = px[xx, yy] < 128
                dark += v
                n += 1
                if prev is not None and v != prev:
                    trans += 1
                prev = v
        return (dark / max(n, 1), trans)

    best = None
    for s in (180, 220, 260):
        for y in range(0, h - s, 20):
            for x in range(0, w - s, 20):
                r, t = score(x, y, s)
                if 0.30 < r < 0.62 and t > s * 0.8:
                    if best is None or t > best[0]:
                        best = (t, x, y, s)
    if not best:
        return None
    _, x, y, s = best
    pad = 12
    return (max(x - pad, 0), max(y - pad, 0), min(s + pad * 2, w), min(s + pad * 2, h))


async def _logged_in(page) -> bool:
    """登录态判定：URL 没被踢到登录页 + 页面出现后台特征文本。"""
    if "login" in (page.url or "").lower():
        return False
    try:
        body = await page.inner_text("body")
    except Exception:  # noqa: BLE001 — 页面切换中取不到正文
        return False
    markers = ("视频号", "发表视频", "内容管理", "视频号ID", "助手")
    return any(m in body for m in markers)


async def run(check_only: bool, qr_out: str = "", timeout_sec: int = 360) -> int:
    from playwright.async_api import async_playwright

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA_DIR),
            headless=check_only,                      # 检查走无头，登录必须有头
            channel="chrome",
            viewport={"width": 1100, "height": 820},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=Translate",
            ],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)

        if check_only:
            ok = await _logged_in(page)
            if ok:
                await ctx.storage_state(path=str(STATE))
                print(f"✅ 登录态有效（持久化 profile）: {page.url}")
                print(f"   已刷新快照: {STATE}")
            else:
                print(f"❌ 登录态已过期: {page.url}")
            print(f"   profile: {USER_DATA_DIR}")
            await ctx.close()
            return 0 if ok else 2

        if await _logged_in(page):
            await ctx.storage_state(path=str(STATE))
            print(f"✅ 登录态仍然有效，无需扫码（已刷新快照 {STATE}）")
            await ctx.close()
            return 0

        # 未登录：去登录页，导出二维码给操作人
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)

        if qr_out:
            shot = await page.screenshot()
            box = _find_qr_box(shot)
            if box:
                from PIL import Image
                import io
                im = Image.open(io.BytesIO(shot))
                im.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])) \
                  .resize((520, 520), Image.LANCZOS).save(qr_out)
                print(f"✅ 二维码已截取 → {qr_out}")
            else:
                Path(qr_out).write_bytes(shot)
                print(f"⚠️ 未定位到二维码，已存整页截图 → {qr_out}")

        print(f"浏览器窗口已打开（持久化 profile: {USER_DATA_DIR}）")
        print("请用微信扫码登录；扫完会自动回写 storage_state。")
        deadline = timeout_sec
        waited = 0
        while waited < deadline:
            await asyncio.sleep(2)
            waited += 2
            if "login" not in (page.url or "").lower() and await _logged_in(page):
                await asyncio.sleep(3)
                await ctx.storage_state(path=str(STATE))
                print(f"✅ 登录成功，登录态已保存: {STATE}")
                await ctx.close()
                return 0
        print(f"❌ 等待登录超时（{deadline}s）")
        await ctx.close()
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查现有登录态（无头，不开窗）")
    ap.add_argument("--qr-out", default=str(PROFILE / "qr.png"),
                    help="二维码图片保存路径（发给操作人扫码）")
    ap.add_argument("--timeout", type=int, default=360, help="等待扫码秒数，默认 360")
    args = ap.parse_args()
    return asyncio.run(run(args.check, args.qr_out, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
