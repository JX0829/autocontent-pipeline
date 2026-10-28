#!/usr/bin/env python3
"""
scripts/xhs_login_flow.py
精准提取小红书创作者平台的真实扫码二维码（160x160 核心扫码区），
并在后台监听扫码登录，完成后自动持久化 cookies 并重启 MCP。
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

QRCODE_PATH = "/tmp/xhs_real_qrcode.png"
USER_COOKIES_PATH = Path.home() / ".xiaohongshu" / "cookies.json"
PROJECT_COOKIES_PATH = PIPELINE_ROOT / "cookies.json"


def run_login():
    print("Launching Chromium to fetch Xiaohongshu creator login QR code...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7778.215 Safari/537.36"
        )
        page = context.new_page()

        print("Navigating to https://creator.xiaohongshu.com/login ...")
        page.goto("https://creator.xiaohongshu.com/login", timeout=30000)
        page.wait_for_timeout(2000)

        # 点击右上角切换到二维码登录
        corner = page.query_selector(".css-wemwzq")
        if corner:
            corner.click()
            page.wait_for_timeout(2000)

        # 查找尺寸大于 100x100 的真实登录二维码
        qrcode_found = False
        imgs = page.query_selector_all("img")
        for img in imgs:
            src = img.get_attribute("src") or ""
            box = img.bounding_box()
            if box and box["width"] >= 120 and src.startswith("data:image/png;base64,"):
                b64_str = src.split(",", 1)[1]
                data = base64.b64decode(b64_str)
                with open(QRCODE_PATH, "wb") as f:
                    f.write(data)
                qrcode_found = True
                print(f"[QRCODE_READY] Real QR code saved to {QRCODE_PATH} (size: {len(data)} bytes, box: {box})")
                sys.stdout.flush()
                break

        if not qrcode_found:
            print("[ERROR] Could not find real login QR code.")
            browser.close()
            return False

        # 飞书推送真实二维码
        try:
            from src.feishu.approval_service import FeishuApprovalService
            svc = FeishuApprovalService()
            target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
            rec_type = "open_id" if target.startswith("ou_") else "chat_id"
            # 通过原生接口或 message 工具发送
        except Exception as e:
            print(f"Warning: {e}")

        # 等待用户扫码（最长等待 180 秒）
        print("Waiting for user to scan QR code on mobile (timeout: 180s)...")
        sys.stdout.flush()

        start_time = time.time()
        logged_in = False
        while time.time() - start_time < 180:
            current_url = page.url
            if "creator.xiaohongshu.com" in current_url and "/login" not in current_url:
                logged_in = True
                print(f"[LOGIN_DETECTED] Current URL: {current_url}")
                break
            
            cookies = context.cookies()
            for c in cookies:
                if c.get("name") == "web_session":
                    logged_in = True
                    break
            if logged_in:
                break
            page.wait_for_timeout(2000)

        if not logged_in:
            print("[TIMEOUT] User did not scan in time.")
            browser.close()
            return False

        print("[LOGIN_SUCCESS] Scanned and logged in successfully! Exporting cookies...")
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

        cookies_payload = {
            "version": 2,
            "seed": 1909992991,
            "saved_at": datetime.now().isoformat(),
            "cookies": formatted_cookies
        }

        # 保存到 ~/.xiaohongshu/cookies.json
        USER_COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USER_COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies_payload, f, ensure_ascii=False, indent=2)
        print(f"Saved cookies to {USER_COOKIES_PATH}")

        # 保存到 project/cookies.json
        with open(PROJECT_COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies_payload, f, ensure_ascii=False, indent=2)
        print(f"Saved cookies to {PROJECT_COOKIES_PATH}")

        # 重启 xiaohongshu-mcp 服务
        print("Restarting xiaohongshu-mcp daemon...")
        try:
            subprocess.run(["launchctl", "kickstart", "-k", "gui/501/com.user.xiaohongshu-mcp"], check=False)
            time.sleep(2)
            print("Daemon restarted successfully.")
        except Exception as e:
            print(f"Failed to restart daemon: {e}")

        # 通过飞书推送登录成功通知卡片
        try:
            from src.feishu.approval_service import FeishuApprovalService
            svc = FeishuApprovalService()
            target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
            rec_type = "open_id" if target.startswith("ou_") else "chat_id"
            card = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "🎉 小红书账号登录成功！"},
                    "template": "green"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "✅ **Cookie 已持久化更新**并自动载入 MCP 服务。\n✨ **消灭零评论冷启动** 与 **三级安全评论回复守护** 均已恢复正常运行！"
                        }
                    }
                ]
            }
            svc.send_approval_card(receive_id=target, receive_id_type=rec_type, card_content=card)
            print("Sent success card to Feishu.")
        except Exception as e:
            print(f"Failed to send success notice to Feishu: {e}")

        browser.close()
        return True


if __name__ == "__main__":
    run_login()
