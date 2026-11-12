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
sys.path.insert(0, str(PROJ))
LOGIN_URL = "https://channels.weixin.qq.com/platform/post/create"


def resolve_profile(account_id: str | None = None) -> tuple[Path, dict]:
    """按账号 ID / 首个启用视频号账号解析浏览器 profile 目录（禁止硬编码账号）。

    2026-11-12：新增教育号（edu）后，本脚本写死 channels-tech 会让教育号无法扫码登录，
    故改为从 config/accounts.yaml 的 profile_path 取。
    """
    from src.utils.accounts import AccountConfigError, by_id, for_platform
    try:
        acc = by_id(account_id) if account_id else for_platform("channels")
    except AccountConfigError as exc:
        raise SystemExit(f"❌ {exc}") from exc
    if not acc:
        raise SystemExit(f"❌ config/accounts.yaml 里没有账号 {account_id}")
    raw = str(acc.get("profile_path") or "").strip()
    if not raw:
        raise SystemExit(f"❌ 账号 {acc.get('id')} 未配置 profile_path")
    profile = Path(raw)
    if not profile.is_absolute():
        profile = (PROJ / raw).resolve()
    return profile, acc



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


async def run(check_only: bool, qr_out: str = "", profile: Path | None = None) -> int:
    from playwright.async_api import async_playwright

    profile = profile or (PROJ / "data" / "browser_profiles" / "channels-tech")
    state = profile / "storage_state.json"
    profile.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=check_only, channel="chrome",
                                          args=["--no-sandbox",
                                                "--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            storage_state=str(state) if state.exists() else None,
            viewport={"width": 1100, "height": 820})
        page = await ctx.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)

        if check_only:
            ok = "login" not in page.url.lower()
            print(("✅ 登录态有效: " if ok else "❌ 登录态已过期: ") + page.url)
            await browser.close()
            return 0 if ok else 2

        # 把二维码截成图片，供发给操作人扫码
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

        print("浏览器已打开，请用微信扫码登录视频号助手…")
        print("登录成功（页面进入发布页）后本脚本会自动保存登录态。")
        for _ in range(180):                      # 最多等 6 分钟
            await asyncio.sleep(2)
            if "login" not in page.url.lower() and "platform" in page.url:
                await asyncio.sleep(3)
                await ctx.storage_state(path=str(state))
                print(f"✅ 登录态已保存: {state}")
                await browser.close()
                return 0
        print("❌ 等待登录超时")
        await browser.close()
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查现有登录态")
    ap.add_argument("--account", default=None,
                    help="账号 ID（缺省取首个启用的视频号账号；教育号填 channels-edu-textbook）")
    ap.add_argument("--qr-out", default=None,
                    help="二维码图片保存路径（默认存到该账号 profile 目录下 qr.png）")
    args = ap.parse_args()

    profile, acc = resolve_profile(args.account)
    qr_out = args.qr_out or str(profile / "qr.png")
    print(f"账号: {acc.get('id')}（type={acc.get('account_type') or '未标注'}）")
    print(f"profile: {profile}")
    return asyncio.run(run(args.check, qr_out, profile))


if __name__ == "__main__":
    sys.exit(main())
