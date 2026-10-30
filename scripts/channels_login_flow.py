#!/usr/bin/env python3
"""
scripts/channels_login_flow.py
微信视频号助手 (channels.weixin.qq.com) 扫码登录与会话持久化辅助脚本
使用 Playwright 启动无头/独立浏览器，打开视频号助手登录页，截取并输出登录二维码至飞书
"""

import os
import sys
import time
import json
import asyncio
from pathlib import Path
from dotenv import load_dotenv

project_root = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
load_dotenv(project_root / ".env")
sys.path.insert(0, str(project_root))

from playwright.async_api import async_playwright
from src.feishu.approval_service import FeishuApprovalService

DATA_DIR = project_root / "data" / "browser_profiles" / "channels-tech"
DATA_DIR.mkdir(parents=True, exist_ok=True)
QR_PATH = DATA_DIR / "channels_login_qr.png"
STATE_PATH = DATA_DIR / "storage_state.json"

CHANNELS_LOGIN_URL = "https://channels.weixin.qq.com/login.html"

async def main():
    print("🚀 Starting Playwright for WeChat Channels login flow...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        print(f"Navigating to {CHANNELS_LOGIN_URL}...")
        await page.goto(CHANNELS_LOGIN_URL, wait_until="networkidle")
        await asyncio.sleep(2)

        # 尝试寻找二维码元素或者直接对登录框/页面截屏
        qr_elem = await page.query_selector(".qrcode-img, .login__type__qrcode__img, img[src*='qrcode'], .qrcode, .login-qrcode-wrap img")
        if qr_elem:
            await qr_elem.screenshot(path=str(QR_PATH))
            print(f"✅ QR Code element captured to: {QR_PATH}")
        else:
            await page.screenshot(path=str(QR_PATH))
            print(f"✅ Full page screenshot captured to: {QR_PATH}")

        # 输出原生飞书媒体标记供系统内联展现
        print(f"MEDIA:{QR_PATH}")

        print("Waiting for user to scan QR Code (timeout 180s)...")
        start_time = time.time()
        logged_in = False
        while time.time() - start_time < 180:
            current_url = page.url
            if "channels.weixin.qq.com/platform" in current_url or "channels.weixin.qq.com/creator" in current_url:
                logged_in = True
                print("🎉 Login detected! Current URL:", current_url)
                break
            # 检查是否有登录成功标识
            success_elem = await page.query_selector(".user-info, .account-name, .header-avatar")
            if success_elem:
                logged_in = True
                print("🎉 Login avatar detected!")
                break
            await asyncio.sleep(3)

        if logged_in:
            await context.storage_state(path=str(STATE_PATH))
            print(f"✅ Storage state saved to: {STATE_PATH}")
            try:
                svc = FeishuApprovalService()
                target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
                rec_type = "open_id" if target.startswith("ou_") else "chat_id"
                svc.send_text_message(
                    receive_id=target,
                    receive_id_type=rec_type,
                    text="🎉 微信视频号创作者助手登录成功！会话已持久化归档，流水线已就绪可执行自动草稿上传。"
                )
            except Exception:
                pass
        else:
            print("❌ Scan timeout or cancelled.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
