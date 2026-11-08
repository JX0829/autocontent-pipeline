"""
src/providers/browser_session.py
CDP 浏览器会话自愈与登录态固化工具

职责：
1. 探测 9222 调试端口是否存活；
2. 端口空置时按需自动拉起独立浏览器（Brave 优先，回退 Chrome），使用专用 profile 目录，
   绝不占用/污染用户日常浏览器与 Agent-Reach；
3. 登录成功后把 storage_state 固化落盘（cookies + localStorage）；
4. 下次启动时自动回灌 storage_state，实现免扫码自愈。
"""

import os
import json
import time
import shutil
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

logger = logging.getLogger("BrowserSession")

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_PROFILE_DIR = "data/browser_profiles/xhs-cdp"
STATE_FILENAME = "storage_state.json"

BROWSER_CANDIDATES = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def pick_browser_executable() -> Optional[str]:
    """按优先级挑选可用浏览器可执行文件"""
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    found = shutil.which("brave") or shutil.which("google-chrome") or shutil.which("chromium")
    return found


def cdp_alive(cdp_url: str = DEFAULT_CDP_URL, timeout: float = 1.5) -> bool:
    """探测 CDP 调试端口是否存活"""
    try:
        req = urllib.request.Request(f"{cdp_url}/json/version", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def launch_cdp_browser(
    cdp_url: str = DEFAULT_CDP_URL,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    headless: bool = True,
) -> bool:
    """
    拉起一个独立浏览器实例占用调试端口。使用专用 user-data-dir，与用户日常浏览器隔离。
    """
    exe = pick_browser_executable()
    if not exe:
        logger.error("未找到任何可用的 Chrome / Brave / Chromium 可执行文件")
        return False

    try:
        port = str(cdp_url.rsplit(":", 1)[-1])
    except Exception:
        port = "9222"

    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={os.path.abspath(profile_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate,MediaRouter",
        "--window-size=1280,900",
        "--window-position=0,0",
    ]
    if headless:
        args.append("--headless=new")

    logger.info(f"按需拉起 CDP 浏览器: {os.path.basename(exe)} port={port} profile={profile_dir}")
    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logger.error(f"拉起浏览器失败: {e}")
        return False

    for _ in range(30):
        if cdp_alive(cdp_url):
            logger.info(f"CDP 端口已就绪: {cdp_url}")
            return True
        time.sleep(0.5)
    logger.error(f"CDP 端口拉起超时: {cdp_url}")
    return False


def ensure_cdp(cdp_url: str = DEFAULT_CDP_URL, profile_dir: str = DEFAULT_PROFILE_DIR,
               headless: bool = True) -> bool:
    """确保 CDP 浏览器可用：存活则直接复用，空置则自动拉起"""
    if cdp_alive(cdp_url):
        return True
    return launch_cdp_browser(cdp_url=cdp_url, profile_dir=profile_dir, headless=headless)


def state_path(profile_dir: str = DEFAULT_PROFILE_DIR) -> Path:
    return Path(profile_dir) / STATE_FILENAME


def persist_storage_state(cdp_url: str = DEFAULT_CDP_URL,
                          profile_dir: str = DEFAULT_PROFILE_DIR) -> bool:
    """登录后将 storage_state（cookies + localStorage）固化落盘"""
    sp = state_path(profile_dir)
    sp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            ctx.storage_state(path=str(sp))
            browser.close()
        size = sp.stat().st_size
        logger.info(f"storage_state 已固化落盘: {sp} ({size} bytes)")
        return size > 0
    except Exception as e:
        logger.error(f"storage_state 固化失败: {e}")
        return False


def restore_cookies(cdp_url: str = DEFAULT_CDP_URL,
                    profile_dir: str = DEFAULT_PROFILE_DIR,
                    domains: tuple = ("xiaohongshu.com",)) -> bool:
    """若有落盘状态，自动回灌 cookies 到当前浏览器上下文"""
    sp = state_path(profile_dir)
    if not sp.exists():
        logger.info("无落盘 storage_state，跳过回灌")
        return False
    try:
        state = json.loads(sp.read_text())
        cookies = state.get("cookies", [])
        if not cookies:
            return False
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            ctx.add_cookies(cookies)
            browser.close()
        logger.info(f"已回灌 {len(cookies)} 条 cookie（匹配域名 {domains}）")
        return True
    except Exception as e:
        logger.warning(f"cookie 回灌失败: {e}")
        return False


def has_session_cookie(cdp_url: str = DEFAULT_CDP_URL,
                       cookie_name: str = "web_session",
                       min_len: int = 10) -> bool:
    """检查当前浏览器上下文中是否存在有效登录 session cookie"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            ok = False
            for ctx in browser.contexts:
                for c in ctx.cookies():
                    if c.get("name") == cookie_name and len(c.get("value") or "") > min_len:
                        ok = True
                        break
                if ok:
                    break
            browser.close()
            return ok
    except Exception as e:
        logger.debug(f"session cookie 检查失败: {e}")
        return False
