"""视频号浏览器会话：固定 CDP 端口 + 持久化 profile 的唯一入口。

为什么要有这个模块（2026-11-12 改造）：
    登录脚本 `scripts/channels_login.py` 改成了持久化 user-data-dir，但发布侧
    `src/providers/channels_cdp.py` 还在用 `storage_state.json` 快照 —— 两套状态
    各自过期，表现为「--check 说登录有效，发布却跳登录页」，且登录脚本每次退出
    就把浏览器关掉，下一次发布又要重开（连发两条必有一次踩扫码/冷启动）。

统一后的契约：
    · **一个常驻 Chrome**，固定端口 `9334`（9333 给抖音，9222 给 Agent-Reach），
      `--user-data-dir` 指向持久化 profile；
    · 登录脚本与发布 provider **都**用 `connect_over_cdp` 复用它，谁都不关浏览器；
    · profile 被别的进程占住（另一个 Chrome/上一次没退干净的会话）时给出可执行的
      提示，而不是抛 Playwright 的原始 SingletonLock 报错；
    · `storage_state.json` 只作为排障备份（评论轮询脚本仍在读它），不再是发布依据。

用法：
    from src.providers.channels_session import ensure, probe, ChannelsSessionError
    cdp = ensure(headless=True)          # 没起来就拉起，返回 http://127.0.0.1:9334
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent.parent

logger = logging.getLogger(__name__)

#: 视频号固定调试端口（与抖音 9333、Agent-Reach 9222 并列）
CDP_PORT = int(os.getenv("CHANNELS_CDP_PORT") or 9334)
CDP_URL = os.getenv("CHANNELS_CDP_URL") or f"http://127.0.0.1:{CDP_PORT}"

#: 持久化 profile（env 覆盖仅用于测试，不要在生产里指到别处）
PROFILE_ROOT = Path(os.getenv("CHANNELS_PROFILE_DIR")
                    or PROJ / "data" / "browser_profiles" / "channels-tech")
USER_DATA_DIR = PROFILE_ROOT / "chrome_profile"
STORAGE_STATE = PROFILE_ROOT / "storage_state.json"
LAUNCH_LOG = Path("/tmp/channels_chrome_9334.log")
PID_FILE = PROFILE_ROOT / "chrome.pid"

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)

HOME_URL = "https://channels.weixin.qq.com/platform"
LOGIN_URL = "https://channels.weixin.qq.com/platform/post/create"


async def is_logged_in(page) -> bool:
    """登录态判定：URL 没被踢到登录页 + 页面出现后台特征文本。

    登录脚本与发布 provider 共用这一份判据，避免「检查说有登录、发布跳登录页」。
    """
    if "login" in (page.url or "").lower():
        return False
    try:
        body = await page.inner_text("body")
    except Exception:  # noqa: BLE001 — 页面切换中取不到正文
        return False
    markers = ("视频号", "发表视频", "内容管理", "视频号ID", "助手")
    return any(m in body for m in markers)


async def verify_login(cdp_url: str = "", *, headless: bool = True) -> tuple[bool, str]:
    """开一个临时页真访问后台，返回 (是否已登录, 当前 URL)。

    只探 CDP 端口不算「登录可用」——端口活着但会话过期是最常见的误判来源。
    """
    from playwright.async_api import async_playwright

    url = cdp_url or ensure(headless=headless)
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(url)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            return (await is_logged_in(page), page.url)
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass


class ChannelsSessionError(RuntimeError):
    """会话不可用，message 已经是可以直接展示给操作人的中文提示。"""


# --------------------------------------------------------------------- 探测

def probe(timeout: float = 1.5) -> dict | None:
    """GET /json/version：端口上有没有活着的 CDP，返回版本信息或 None。"""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # 探测失败是常态（浏览器没起来），但要看得出为什么，否则会误判成「死了」
        logger.debug(f"CDP 探测失败（{CDP_URL}）：{type(exc).__name__}: {exc}")
        return None


def _process_cmdline(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"ps 取 cmdline 失败（pid={pid}）：{type(exc).__name__}: {exc}")
        return ""
    return out.strip()


def lock_holder() -> tuple[int, bool, bool] | None:
    """读 Chrome 的 SingletonLock，返回 (pid, 进程是否活着, 该进程是否真在用这个 profile)。

    Chrome 在 user-data-dir 里写 `SingletonLock -> <hostname>-<pid>` 软链。两种「假占用」：
      · 进程已退出 → 悬空软链（上次异常退出），可直接清掉重开；
      · **profile 被整目录拷贝过**（备份/测试副本）→ 软链里是原机的 pid，
        那个进程活着但根本没在用这份目录。所以必须核对 cmdline 里的 user-data-dir，
        否则会把「拷贝出来的 profile」误判成被占用（实测踩过）。
    """
    lock = USER_DATA_DIR / "SingletonLock"
    try:
        target = os.readlink(lock)
    except OSError as exc:
        logger.debug(f"读不到 SingletonLock（{lock}）：{type(exc).__name__}: {exc}")
        return None
    pid_txt = target.rsplit("-", 1)[-1]
    if not pid_txt.isdigit():
        return None
    pid = int(pid_txt)
    try:
        os.kill(pid, 0)
    except OSError as exc:
        logger.debug(f"锁里的 pid={pid} 已不在（{type(exc).__name__}），判定悬空锁")
        return (pid, False, False)
    cmdline = _process_cmdline(pid)
    owns = f"user-data-dir={USER_DATA_DIR}" in cmdline
    return (pid, True, owns)


def _clear_stale_lock() -> None:
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (USER_DATA_DIR / name).unlink()
        except OSError as exc:
            logger.debug(f"清理锁文件 {name} 失败（无所谓）：{type(exc).__name__}: {exc}")


def _cmdline_holders() -> list[int]:
    """兜底：按 user-data-dir 抓进程（lock 被清掉但进程还在的极端情况）。"""
    try:
        out = subprocess.run(["pgrep", "-f", f"user-data-dir={USER_DATA_DIR}"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"pgrep 兜底查进程失败：{type(exc).__name__}: {exc}")
        return []
    return [int(x) for x in out.split() if x.strip().isdigit()]


def _chrome_binary() -> str:
    env = os.getenv("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for cand in CHROME_CANDIDATES:
        if Path(cand).exists():
            return cand
    raise ChannelsSessionError(
        "找不到 Chrome/Chromium 可执行文件；用 CHROME_PATH 指定，或安装 Google Chrome")


# --------------------------------------------------------------------- 启动

def _busy_message(pid: int) -> str:
    return (
        f"视频号浏览器 profile 正被另一个进程占用（pid={pid}）。\n"
        f"  占用目录: {USER_DATA_DIR}\n"
        f"  原因通常是：另有一个 channels_login.py / 发布脚本开着同一 profile，"
        f"或上次会话没退干净。\n"
        f"  处理办法（任选其一）：\n"
        f"    1) 关掉那个 Chrome 窗口后重试；\n"
        f"    2) 终端执行 `kill {pid}`（只杀这个进程）；\n"
        f"    3) 运行 `python3 scripts/channels_login.py --stop` 由脚本收尾。"
    )


def launch(headless: bool = False, timeout: float = 25.0) -> None:
    """拉起常驻 Chrome（带远端调试端口）。已起来则什么都不做。"""
    if probe():
        return

    holder = lock_holder()
    if holder and holder[1] and holder[2]:
        raise ChannelsSessionError(_busy_message(holder[0]))
    # 悬空锁 / 拷贝出来的 profile 里的旧锁：都清掉，避免「看起来被占用」
    _clear_stale_lock()

    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        _chrome_binary(),
        f"--user-data-dir={USER_DATA_DIR}",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=Translate",
        "--disable-backgrounding-occluded-windows",
        "--window-size=1100,820",
    ]
    if headless:
        cmd.append("--headless=new")

    LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = LAUNCH_LOG.open("a", encoding="utf-8")
    log.write(f"\n=== launch {time.strftime('%Y-%m-%d %H:%M:%S')} headless={headless} ===\n")
    log.flush()
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)  # 脱离当前进程组：脚本退出不带走浏览器

    deadline = time.time() + timeout
    while time.time() < deadline:
        if probe():
            PID_FILE.write_text(str(proc.pid), encoding="utf-8")
            return
        if proc.poll() is not None and not probe():
            break
        time.sleep(0.5)

    holder = lock_holder()
    if holder and holder[1] and holder[2]:
        raise ChannelsSessionError(_busy_message(holder[0]))
    raise ChannelsSessionError(
        f"Chrome 未能在 {timeout:.0f}s 内就绪（{CDP_URL}）。\n"
        f"  启动日志: {LAUNCH_LOG}\n"
        f"  常见原因：profile 被占用、端口 {CDP_PORT} 被别的程序占、Chrome 版本不支持 --headless=new")


def ensure(headless: bool = False, timeout: float = 25.0) -> str:
    """保证 CDP 可用并返回地址；不可用时抛 ChannelsSessionError（消息可直接展示）。"""
    if probe():
        return CDP_URL
    launch(headless=headless, timeout=timeout)
    return CDP_URL


def stop() -> tuple[bool, str]:
    """收尾：杀掉**确实在用这个 profile** 的进程。返回 (是否动手, 说明)。"""
    pids: set[int] = set()
    holder = lock_holder()
    if holder and holder[1] and holder[2]:
        pids.add(holder[0])
    pids.update(_cmdline_holders())
    if not pids:
        return (False, "没有发现占用该 profile 的进程（锁可能是悬空/拷贝残留，已清理）" if
                (lock_holder() is not None) else "没有发现占用该 profile 的进程")
    for pid in sorted(pids):
        try:
            os.kill(pid, 15)
        except OSError as exc:
            logger.debug(f"kill -15 pid={pid} 失败（可能已退出）：{type(exc).__name__}")
            continue
    for _ in range(20):
        if probe() is None:
            break
        time.sleep(0.3)
    _clear_stale_lock()
    try:
        PID_FILE.unlink()
    except OSError as exc:
        logger.debug(f"删 pid 文件失败（无所谓）：{type(exc).__name__}: {exc}")
    return (True, f"已结束进程 {', '.join(str(p) for p in sorted(pids))}")
