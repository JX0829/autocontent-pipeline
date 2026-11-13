#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号登录：**常驻 Chrome（CDP 9334）+ 持久化 profile**（2026-11-12 改造）

为什么这样改：
    上一版用 `launch_persistent_context` 每次新开浏览器、退出即关窗口，于是
      · 登录脚本与发布侧各持一套状态（一个 profile、一个 storage_state 快照），互相脱节；
      · 连发两条视频时，第一条结束时浏览器被关掉，第二条又要冷启动（甚至撞 profile 占用）；
      · profile 被占用时抛的是 Playwright/Chrome 原始报错，看不出下一步该做什么。

现在：
    · 启动一次常驻 Chrome，固定 `--remote-debugging-port=9334`、`user-data-dir` 指向
      `data/browser_profiles/channels-tech/chrome_profile`；
    · 本脚本与 `src/providers/channels_cdp.py` 都 `connect_over_cdp` 复用它，**谁也不关浏览器**；
    · 关窗后 `--check` 会自动把它拉起来（cookie 在 profile 里，不需要重新扫码）；
    · profile 被别的进程占用时输出可执行的三选一提示（关窗 / kill pid / `--stop`）。
    · `storage_state.json` 仍会在登录成功时刷一份，但只作排障备份（评论轮询脚本在读它），
      发布链路不再依赖它。

用法：
  python3 scripts/channels_login.py               # 需要时开窗/复用窗口，人工扫码
  python3 scripts/channels_login.py --check       # 只检查（必要时无头自愈拉起 Chrome）
  python3 scripts/channels_login.py --stop        # 结束占用该 profile 的进程
  python3 scripts/channels_login.py --qr-out /path/qr.png --timeout 300
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.providers import channels_session  # noqa: E402
from src.providers.channels_session import (  # noqa: E402
    CDP_URL, HOME_URL, LOGIN_URL, STORAGE_STATE, USER_DATA_DIR, ChannelsSessionError,
    ensure, is_logged_in,
)


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


async def _open_page(pw):
    """连到常驻 Chrome 并在默认上下文里开一个新的空白页（不新建上下文、不复用状态）。"""
    browser = await pw.chromium.connect_over_cdp(CDP_URL)
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    page = await ctx.new_page()
    return browser, ctx, page


async def _refresh_snapshot(ctx) -> None:
    """刷一份 storage_state 备份（仅排障/评论轮询用，失败不影响登录结论）。"""
    try:
        STORAGE_STATE.parent.mkdir(parents=True, exist_ok=True)
        await ctx.storage_state(path=str(STORAGE_STATE))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 备份 storage_state 失败（不影响登录）: {exc}")


async def run(check_only: bool, qr_out: str = "", timeout_sec: int = 360) -> int:
    from playwright.async_api import async_playwright

    # 关键：ensure 会复用已在跑的 9334；关窗了就把它拉起来（--check 用无头，避免打扰）
    try:
        ensure(headless=check_only)
    except ChannelsSessionError as exc:
        print(f"❌ {exc}")
        return 3

    async with async_playwright() as p:
        browser, ctx, page = await _open_page(p)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)

            if await is_logged_in(page):
                await _refresh_snapshot(ctx)
                print(f"✅ 登录态有效（持久化 profile，复用 CDP {CDP_URL}）: {page.url}")
                print(f"   profile: {USER_DATA_DIR}")
                return 0

            if check_only:
                print(f"❌ 登录态已失效: {page.url}")
                print("   无头检查不能扫码；去掉 --check 跑一次即可开窗扫码。")
                return 2

            # 未登录：去发布页拿二维码，导出给操作人
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)

            if qr_out:
                shot = await page.screenshot()
                box = _find_qr_box(shot)
                if box:
                    import io

                    from PIL import Image

                    im = Image.open(io.BytesIO(shot))
                    Path(qr_out).parent.mkdir(parents=True, exist_ok=True)
                    im.crop((box[0], box[1], box[0] + box[2], box[1] + box[3])) \
                      .resize((520, 520), Image.LANCZOS).save(qr_out)
                    print(f"✅ 二维码已截取 → {qr_out}")
                else:
                    Path(qr_out).write_bytes(shot)
                    print(f"⚠️ 未定位到二维码，已存整页截图 → {qr_out}")

            print(f"浏览器窗口（CDP {CDP_URL}）已就绪，请用微信扫码登录。")
            waited = 0
            while waited < timeout_sec:
                await asyncio.sleep(2)
                waited += 2
                if "login" not in (page.url or "").lower() and await is_logged_in(page):
                    await asyncio.sleep(3)
                    await _refresh_snapshot(ctx)
                    print("✅ 登录成功，登录态已在持久化 profile 内（浏览器保持开启，供发布复用）")
                    return 0
            print(f"❌ 等待登录超时（{timeout_sec}s）")
            return 1
        finally:
            # 只关这个页面，**不关浏览器**：9334 要留给后续发布复用
            try:
                await page.close()
            except Exception:  # noqa: BLE001 — 页面已被手动关闭
                pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查登录态（必要时无头自愈拉起 Chrome）")
    ap.add_argument("--stop", action="store_true", help="结束占用该 profile 的进程后退出")
    ap.add_argument("--qr-out", default=str(channels_session.PROFILE_ROOT / "qr.png"),
                    help="二维码图片保存路径（发给操作人扫码）")
    ap.add_argument("--timeout", type=int, default=360, help="等待扫码秒数，默认 360")
    args = ap.parse_args()

    if args.stop:
        acted, msg = channels_session.stop()
        print(("✅ " if acted else "ℹ️ ") + msg)
        return 0
    return asyncio.run(run(args.check, args.qr_out, args.timeout))


if __name__ == "__main__":
    sys.exit(main())
