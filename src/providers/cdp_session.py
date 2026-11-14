# -*- coding: utf-8 -*-
"""CDP 关卡：探活 / 端点解析 / 单实例互斥 / 借用常驻 Chrome 的包装器（2026-11-13）

为什么需要这一层：
    抖音 provider 每次 `p.chromium.launch()` 起**全新临时 profile**，登录态只靠
    `storage_state.json` 快照维持。快照 cookie 写着 2027-01-09 但服务端会话早失效，
    于是天天 `LOGIN_REQUIRED`（llamacpp-v1-1 实测）。
    正解和视频号一样：人工登录一次 → 浏览器 profile 持久保留 → 流水线用
    `connect_over_cdp` **接管那一个窗口**，不再重建环境。

这一层只做四件与平台无关的事：
    · `probe()`          探活（GET /json/version），掉线时给可执行提示，不抛长栈
    · `resolve()`        端点解析：账号配置 > 环境变量 > 内置默认（空=关，走老逻辑）
    · `exclusive()`      同一端口同一时刻只允许一个发布流程（flock，抢不到 → CDP_BUSY）
    · `connect()`        接管常驻 Chrome，返回**不会把浏览器关掉**的 context/browser 包装器
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_DIR = PROJECT_ROOT / "data" / "locks"


class CdpError(RuntimeError):
    """CDP 相关错误的基类，`code` 供上层做机器判定（不靠匹配文案）。"""

    code = "CDP_ERROR"


class CdpUnavailable(CdpError):
    code = "CDP_UNAVAILABLE"


class CdpBusy(CdpError):
    code = "CDP_BUSY"


class LoginRequired(CdpError):
    code = "LOGIN_REQUIRED"


def normalize(cdp_url: str | None) -> str:
    return (cdp_url or "").strip().rstrip("/")


def resolve(account_config: dict[str, Any], env_key: str, default: str = "") -> str:
    """端点优先级：账号配置 > 环境变量 > 内置默认。空串 = 关闭 CDP 模式（走老逻辑）。"""
    for raw in (account_config.get("cdp_url"), os.getenv(env_key), default):
        val = normalize(raw if isinstance(raw, str) else None)
        if val:
            return val
    return ""


def probe(cdp_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """`GET /json/version` 探活。返回 (是否可用, 说明)。**任何异常都不外抛**。"""
    url = f"{normalize(cdp_url)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
        data = json.loads(raw)
        return True, f"{data.get('Browser', '?')} (websocket {str(data.get('webSocketDebuggerUrl', '?'))[:32]}…)"
    except urllib.error.URLError as exc:
        return False, f"连不上 {url}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — 探活失败必须是可读结论，不是栈
        return False, f"探活失败 {url}: {type(exc).__name__}: {exc}"


def port_of(cdp_url: str) -> str:
    return normalize(cdp_url).rsplit(":", 1)[-1].split("/")[0] or "0"


@contextmanager
def exclusive(name: str, cdp_url: str, wait: float = 0.0) -> Iterator[None]:
    """端口级单实例互斥（R5）。

    抢不到锁 → `CdpBusy`，绝不排队抢同一个 tab；`wait>0` 时最多等这么多秒。
    锁文件放 `data/locks/`，进程崩溃由内核自动释放（flock 语义），不会留死锁。
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{name}-{port_of(cdp_url)}.lock"
    fh = open(lock_path, "w", encoding="utf-8")
    try:
        deadline_hit = False
        if wait > 0:
            import time as _t
            end = _t.time() + wait
            while _t.time() < end:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    _t.sleep(0.5)
            else:
                deadline_hit = True
        else:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    deadline_hit = True
                else:
                    raise
        if deadline_hit:
            holder = lock_path.read_text(encoding="utf-8").strip() or "另一个流程"
            raise CdpBusy(f"{name} 的 CDP 会话正被占用（{normalize(cdp_url)}，持有者 {holder}）："
                          f"同一时刻只允许一个发布流程；等它跑完或确认它已死后重试")
        fh.write(f"pid={os.getpid()} at={__import__('time').strftime('%H:%M:%S')}")
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


class _BorrowedContext:
    """接管来的 context：记下自己 new_page 的页面，收尾只关这些页面。"""

    def __init__(self, ctx, browser: "_BorrowedBrowser"):
        self._ctx = ctx
        self._browser = browser

    def new_page(self, *a, **kw):
        page = self._ctx.new_page(*a, **kw)
        self._browser._pages.append(page)
        return page

    def __getattr__(self, item):
        return getattr(self._ctx, item)


class _BorrowedBrowser:
    """接管来的 browser：`close()` **不会关掉常驻 Chrome**（profile 必须留着）。

    抖音 provider 里到处都是 `browser.close()`。真关掉的话，那次登录态就白扫了，
    下一个 run 又回到「起临时 profile → LOGIN_REQUIRED」。所以这里把 close() 降级成
    「关掉本流程开的页面」。
    """

    def __init__(self, browser):
        self._browser = browser
        self._pages: list = []
        self._context = _BorrowedContext(self._pick_context(), self)

    def _pick_context(self):
        contexts = getattr(self._browser, "contexts", None) or []
        if contexts:
            return contexts[0]           # 常驻 profile 的真实 context
        return self._browser.new_context()

    @property
    def context(self) -> _BorrowedContext:
        return self._context

    @property
    def contexts(self):
        return self._browser.contexts

    def new_context(self, *a, **kw):
        return self._browser.new_context(*a, **kw)

    def close(self) -> None:
        for page in list(self._pages):
            try:
                page.close()
            except Exception:  # noqa: BLE001 — 页面可能已被人工关掉
                pass
        self._pages.clear()

    def __getattr__(self, item):
        return getattr(self._browser, item)


def connect(playwright, cdp_url: str) -> tuple[_BorrowedContext, _BorrowedBrowser]:
    """接管常驻 Chrome。探活不过就抛 `CdpUnavailable`（带 profile/端口与人工操作提示）。"""
    ok, detail = probe(cdp_url)
    if not ok:
        raise CdpUnavailable(
            f"CDP 不可用（cdp={normalize(cdp_url)}）：{detail}。"
            f"请先启动带远程调试的 Chrome 并确认已登录（RUNBOOK §5.2）")
    browser = playwright.chromium.connect_over_cdp(normalize(cdp_url))
    holder = _BorrowedBrowser(browser)
    return holder.context, holder
