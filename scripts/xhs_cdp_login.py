#!/usr/bin/env python3
"""
scripts/xhs_cdp_login.py
小红书 CDP 扫码登录 + 登录态固化落盘

流程：
1. 确保 9222 CDP 浏览器可用（存活则复用 Agent-Reach 实例，空置则按需自动拉起）；
2. 打开小红书登录页，截取二维码并输出 MEDIA 路径；
3. 轮询等待扫码登录成功（默认 600s）；
4. 成功后把 storage_state（cookies + localStorage）固化落盘，供后续巡检自动回灌。
"""

import os
import sys
import time
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright
from src.providers import browser_session as bs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("XhsCdpLogin")

CDP_URL = "http://127.0.0.1:9222"
PROFILE_DIR = "data/browser_profiles/xhs-cdp"
QR_PATH = Path(PROFILE_DIR) / "xhs_login_qr.png"
LOGIN_URL = "https://www.xiaohongshu.com/login"


def main(wait_sec: int = 600) -> int:
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)

    if not bs.ensure_cdp(CDP_URL, PROFILE_DIR, headless=True):
        logger.error("CDP 浏览器不可用，终止")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)

        # 截取二维码
        qr = page.query_selector("img.qrcode-img") or page.query_selector(".qrcode")
        if qr:
            qr.screenshot(path=str(QR_PATH))
            try:
                from PIL import Image
                im = Image.open(QR_PATH)
                im.resize((im.width * 3, im.height * 3), Image.LANCZOS).save(QR_PATH)
            except Exception as e:
                logger.warning(f"二维码放大跳过: {e}")
            logger.info(f"二维码已截取: {QR_PATH}")
        else:
            page.screenshot(path=str(QR_PATH))
            logger.warning(f"未定位到二维码元素，已整页截图兜底: {QR_PATH}")

        print(f"MEDIA:{QR_PATH.resolve()}")

        logger.info(f"等待扫码登录（最长 {wait_sec}s）...")
        start = time.time()
        logged_in = False
        while time.time() - start < wait_sec:
            try:
                cur = page.url
            except Exception:
                cur = ""
            if "/login" not in cur and cur.startswith("https://www.xiaohongshu.com"):
                logged_in = True
                logger.info(f"🎉 检测到登录跳转: {cur}")
                break
            # 兜底：dom 中出现已登录标识
            try:
                if page.query_selector(".side-bar .user, .user-info, .reds-avatar") and "/login" not in cur:
                    logged_in = True
                    logger.info("🎉 检测到已登录头像")
                    break
            except Exception:
                pass
            time.sleep(3)

        ctx.close()

    # URL 轮询可能漏判（登录弹窗消失但 URL 未变），超时后做一次权威校验兜底
    if not logged_in:
        logger.info("URL 轮询未捕获跳转，执行 cookie + 受保护页权威校验...")
        try:
            from src.providers.xiaohongshu_cdp import XiaohongshuCDPProvider
            if XiaohongshuCDPProvider(cdp_url=CDP_URL, profile_dir=PROFILE_DIR).check_login_status():
                logged_in = True
                logger.info("✅ 权威校验确认已登录（轮询漏判已兜底）")
        except Exception as e:
            logger.warning(f"权威校验异常: {e}")

    if logged_in:
        ok = bs.persist_storage_state(CDP_URL, PROFILE_DIR)
        sp = bs.state_path(PROFILE_DIR)
        if ok:
            print(f"✅ LOGIN_OK storage_state={sp} ({sp.stat().st_size} bytes)")
            return 0
        print("⚠️ LOGIN_OK 但 storage_state 固化失败")
        return 3

    print("❌ LOGIN_TIMEOUT 扫码等待超时")
    return 1


if __name__ == "__main__":
    wait = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    sys.exit(main(wait))
