#!/usr/bin/env python3
import urllib.request
import json
import time
import os
import sys
from pathlib import Path
import qrcode

SAVE_DIR = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data")
SAVE_DIR.mkdir(parents=True, exist_ok=True)
QR_PATH = SAVE_DIR / "bilibili_login_qr.png"
COOKIE_PATH = SAVE_DIR / "cookies_bilibili.json"
KEY_FILE = SAVE_DIR / "bilibili_qr_key.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def generate_qr():
    req = urllib.request.Request(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
        headers=HEADERS
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    
    if res.get("code") != 0:
        print(f"Failed to generate QR: {res}")
        sys.exit(1)
        
    url = res["data"]["url"]
    qrcode_key = res["data"]["qrcode_key"]
    
    # 生成二维码图片
    img = qrcode.make(url)
    img.save(str(QR_PATH))
    
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        json.dump({"qrcode_key": qrcode_key, "url": url, "created_at": time.time()}, f)
        
    print(f"QR_IMAGE_PATH:{QR_PATH}")
    print(f"QRCODE_KEY:{qrcode_key}")
    return qrcode_key

def poll_login(qrcode_key: str, max_wait: int = 180):
    url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
    print("Waiting for scan...")
    start_t = time.time()
    while time.time() - start_t < max_wait:
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                # 获取 set-cookie
                cookie_headers = resp.headers.get_all("Set-Cookie", [])
                res = json.loads(resp.read().decode("utf-8"))
                code = res["data"]["code"]
                msg = res["data"]["message"]
                if code == 0:
                    print("✅ 登录成功！正在提取并保存 Cookies...")
                    cookies_dict = {}
                    for ch in cookie_headers:
                        parts = ch.split(";")[0].split("=")
                        if len(parts) >= 2:
                            cookies_dict[parts[0].strip()] = "=".join(parts[1:]).strip()
                    with open(COOKIE_PATH, "w", encoding="utf-8") as f:
                        json.dump(cookies_dict, f, ensure_ascii=False, indent=2)
                    print(f"Cookies saved to: {COOKIE_PATH}")
                    return True
                elif code == 86101:
                    # 未扫码
                    pass
                elif code == 86090:
                    print("📱 已扫码，请在手机端点击【确认登录】...")
                elif code == 86038:
                    print("❌ 二维码已失效，请重新生成")
                    return False
        except Exception as e:
            print("Poll err:", e)
        time.sleep(3)
    print("⏰ 等待超时")
    return False

if __name__ == "__main__":
    generate_qr()
