#!/usr/bin/env python3
"""
scripts/live_xhs_login.py
保持浏览器长连接会话，截取二维码并实时监听扫码与登录跳转。
一旦登录成功，自动导出 cookies.json 并重启 xiaohongshu-mcp 服务。
"""

import os
import sys
import time
import json
import base64
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE_ROOT))
load_dotenv(PIPELINE_ROOT / ".env")

from playwright.sync_api import sync_playwright

QRCODE_PATH = "/tmp/xhs_live_qrcode.png"
USER_COOKIES_PATH = Path.home() / ".xiaohongshu" / "cookies.json"
PROJECT_COOKIES_PATH = PIPELINE_ROOT / "cookies.json"

def main():
    print("[1/4] 启动浏览器并打开创作者平台登录页...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7778.215 Safari/537.36"
        )
        page = context.new_page()
        page.goto("https://creator.xiaohongshu.com/login", timeout=30000)
        page.wait_for_timeout(2000)

        # 切换到扫码登录
        corner = page.query_selector(".css-wemwzq")
        if corner:
            corner.click()
            page.wait_for_timeout(2000)

        # 截取登录二维码
        imgs = page.query_selector_all("img")
        qr_saved = False
        for img in imgs:
            box = img.bounding_box()
            if box and box["width"] >= 120:
                img.screenshot(path=QRCODE_PATH)
                qr_saved = True
                print(f"[2/4] 最新登录二维码已截取保存至 {QRCODE_PATH} (box: {box})")
                sys.stdout.flush()
                break

        if not qr_saved:
            print("[ERROR] 未找到登录二维码元素")
            browser.close()
            sys.exit(1)

        print("[3/4] 正在保持长连接会话，等待手机扫码并点击确认 (超时: 180s)...")
        sys.stdout.flush()

        start_time = time.time()
        logged_in = False
        while time.time() - start_time < 180:
            current_url = page.url
            if "creator.xiaohongshu.com" in current_url and "/login" not in current_url:
                logged_in = True
                print(f"[LOGIN_SUCCESS] 检测到页面跳转: {current_url}")
                break

            cookies = context.cookies()
            for c in cookies:
                if c.get("name") == "web_session":
                    logged_in = True
                    print("[LOGIN_SUCCESS] 检测到 web_session Cookie 写入！")
                    break
            if logged_in:
                break
            page.wait_for_timeout(2000)

        if not logged_in:
            print("[TIMEOUT] 等待扫码超时")
            browser.close()
            sys.exit(1)

        print("[4/4] 登录成功！正在提取 Cookies 并持久化...")
        page.wait_for_timeout(2000)

        raw_cookies = context.cookies()
        formatted_cookies = []
        for c in raw_cookies:
            item = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "path": c.get("path", "/"),
                "expires": c.get("expires", -1),
                "size": len(c.get("name", "")) + len(c.get("value", "")),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "session": c.get("expires", -1) == -1,
                "priority": "Medium",
                "sameParty": False,
                "sourceScheme": "Secure" if c.get("secure") else "NonSecure",
                "sourcePort": 443 if c.get("secure") else 80
            }
            formatted_cookies.append(item)

        payload = {
            "version": 2,
            "seed": 1909992991,
            "saved_at": datetime.now().isoformat(),
            "cookies": formatted_cookies
        }

        USER_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USER_COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        with open(PROJECT_COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("Cookies 写入完成！")

        print("正在重启 xiaohongshu-mcp 服务...")
        subprocess.run(["launchctl", "kickstart", "-k", "gui/501/com.user.xiaohongshu-mcp"], check=False)
        time.sleep(2)
        print("xiaohongshu-mcp 重启完毕！")

        browser.close()
        print("全部流程执行完成！")

if __name__ == "__main__":
    main()
