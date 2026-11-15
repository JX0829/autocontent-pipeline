"""发布前置预检：登录态 / CDP 必须在**进发布循环之前**判定（T2）。

为什么要有（2026-11-15）：
    视频号登录过期、抖音 CDP 掉线、B站 cookie 缺失，过去都是「发布循环里跑到第 N 步
    才炸」——一半平台已经发出去、另一半静默失败，回执卡只剩一句「发布失败」，
    人还得自己去猜是哪个平台的登录态。

现在的契约：
    · 在门禁2（审批通过）之后、**占任何台账之前**，对本次要发的平台各做一次探活；
    · 任一平台不可用 → **不进入发布循环、不发布任何内容**；
    · 发一张告警卡（含重新登录/启动命令），结论写进 `publish.json`；
    · 抛 `ExecutorError` → 流水线 exit≠0。

探活口径（只做便宜、无副作用的检查，绝不在这里起浏览器）：
    douyin    9333 CDP 探活 + 持久化 profile 目录存在
    channels  9334 CDP 探活（`channels_session.probe`）+ 登录态快照存在
    bilibili  `data/cookies_bilibili.json` 存在且含 SESSDATA
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJ = Path(__file__).resolve().parent.parent.parent

DOUYIN_CDP = os.getenv("DOUYIN_CDP_URL") or "http://127.0.0.1:9333"
CHANNELS_CDP = os.getenv("CHANNELS_CDP_URL") or "http://127.0.0.1:9334"
BILI_COOKIES = PROJ / "data" / "cookies_bilibili.json"
DOUYIN_PROFILE = PROJ / "data" / "browser_profiles" / "douyin-cdp"
CHANNELS_STATE = PROJ / "data" / "browser_profiles" / "channels-tech" / "storage_state.json"
CHANNELS_CDP_HOME = "https://channels.weixin.qq.com/platform"

FIX_DOUYIN = (
    '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" '
    f'--remote-debugging-port=9333 --user-data-dir={DOUYIN_PROFILE} '
    "--no-first-run --no-default-browser-check about:blank  # 然后在该窗口扫码登录创作者中心"
)
FIX_CHANNELS = (
    f"python3 {PROJ / 'scripts' / 'channels_login.py'} "
    "--qr-out /tmp/qr_channels.png  # 扫码后常驻 Chrome(9334) 会被复用，不用再开"
)

#: 未登录时视频号后台页上的特征文案（2026-11-15 实测：地址栏可能仍是 /platform/post/create，
#: 只看 URL 会误判成已登录 —— 必须看页面内容）
CHANNELS_LOGGED_OUT_HINTS = ("登录视频号助手", "扫码登录")
CHANNELS_LOGGED_IN_HINTS = ("内容管理", "数据中心", "发表动态", "互动管理")
FIX_BILIBILI = f"重新导出 B站登录 cookie 到 {BILI_COOKIES}（需含 SESSDATA）"


def _probe_cdp(url: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{url}/json/version", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # 探测失败是常态（浏览器没起），但要看得出为什么，否则会误判成「死了」
        logging.getLogger(__name__).debug(f"CDP 探测失败（{url}）：{type(exc).__name__}: {exc}")
        return None


def _channels_logged_in(timeout_ms: int = 12000) -> tuple[Optional[bool], str]:
    """连 9334 打开视频号后台，按**页面内容**判登录态（headless 判不了，视频号必须 headful）。

    返回 (True 已登录 / False 未登录 / None 判不了, 说明)。
    踩过的坑：未登录时地址栏也可能是 `/platform/post/create`（落地页），
    只看 URL 会把「未登录」当成「已登录」，这正是本次收口要堵的洞。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001 — 没有 playwright 也要说清楚
        return None, f"playwright 不可用：{type(exc).__name__}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CHANNELS_CDP, timeout=timeout_ms)
            ctx = browser.contexts[0]
            page = ctx.new_page()
            try:
                page.goto(f"{CHANNELS_CDP_HOME}", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                page.wait_for_timeout(2500)
                body = page.inner_text("body")[:1500]
                url = page.url or ""
            finally:
                page.close()
            if "login" in url.lower():
                return False, f"跳登录页 {url[:60]}"
            if any(k in body for k in CHANNELS_LOGGED_OUT_HINTS):
                return False, "页面含「登录视频号助手/扫码登录」"
            if any(k in body for k in CHANNELS_LOGGED_IN_HINTS):
                return True, f"后台可见（{url[:40]}）"
            return None, "页面特征不明确"
    except Exception as exc:  # noqa: BLE001 — 探测异常不等于未登录，交给调用方降级
        return None, f"{type(exc).__name__}: {str(exc)[:60]}"


def probe_douyin() -> Dict[str, Any]:
    ver = _probe_cdp(DOUYIN_CDP)
    if not ver:
        return {"platform": "douyin", "ok": False, "code": "DOUYIN_CDP_UNAVAILABLE",
                "detail": f"{DOUYIN_CDP} 无响应（浏览器没开或调试端口没起）", "fix": FIX_DOUYIN}
    if not DOUYIN_PROFILE.exists():
        return {"platform": "douyin", "ok": False, "code": "DOUYIN_PROFILE_MISSING",
                "detail": f"持久化 profile 不存在：{DOUYIN_PROFILE}", "fix": FIX_DOUYIN}
    return {"platform": "douyin", "ok": True, "code": "",
            "detail": f"CDP ok ({ver.get('Browser', '?')})", "fix": ""}


def probe_channels() -> Dict[str, Any]:
    try:
        from src.providers.channels_session import probe as cs_probe
    except Exception as exc:  # noqa: BLE001 — 导不进来也是一种不可用，要说清楚
        return {"platform": "channels", "ok": False, "code": "CHANNELS_MODULE_ERROR",
                "detail": f"channels_session 不可用：{type(exc).__name__}: {exc}",
                "fix": FIX_CHANNELS}
    if not cs_probe():
        return {"platform": "channels", "ok": False, "code": "CHANNELS_CDP_UNAVAILABLE",
                "detail": f"{CHANNELS_CDP} 无响应（常驻 Chrome 没起）", "fix": FIX_CHANNELS}
    if not CHANNELS_STATE.exists():
        return {"platform": "channels", "ok": False, "code": "CHANNELS_LOGIN_MISSING",
                "detail": f"登录态快照不存在：{CHANNELS_STATE}", "fix": FIX_CHANNELS}
    logged, detail = _channels_logged_in()
    if logged is False:
        return {"platform": "channels", "ok": False, "code": "CHANNELS_NOT_LOGGED_IN",
                "detail": f"后台页显示未登录（{detail}）", "fix": FIX_CHANNELS}
    if logged is None:
        # 判不了（Playwright 不可用等）不拦，但要留痕：别把「没检查」伪装成「检查通过」
        return {"platform": "channels", "ok": True, "code": "CHANNELS_LOGIN_UNKNOWN",
                "detail": f"CDP ok，登录态无法判定（{detail}）", "fix": ""}
    return {"platform": "channels", "ok": True, "code": "",
            "detail": f"CDP ok + 后台页已登录（{detail}）", "fix": ""}


def probe_bilibili() -> Dict[str, Any]:
    if not BILI_COOKIES.exists():
        return {"platform": "bilibili", "ok": False, "code": "BILI_NO_COOKIE",
                "detail": f"缺少 cookie 文件：{BILI_COOKIES}", "fix": FIX_BILIBILI}
    try:
        raw = json.loads(BILI_COOKIES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"platform": "bilibili", "ok": False, "code": "BILI_COOKIE_BROKEN",
                "detail": f"cookie 文件读不出来：{type(exc).__name__}: {exc}", "fix": FIX_BILIBILI}
    blob = json.dumps(raw, ensure_ascii=False)
    if "SESSDATA" not in blob:
        return {"platform": "bilibili", "ok": False, "code": "BILI_NO_SESSDATA",
                "detail": "cookie 里没有 SESSDATA（登录态不完整）", "fix": FIX_BILIBILI}
    return {"platform": "bilibili", "ok": True, "code": "", "detail": "cookie 含 SESSDATA", "fix": ""}


_PROBES = {"douyin": probe_douyin, "channels": probe_channels, "bilibili": probe_bilibili}


def probe_platform(platform: str) -> Dict[str, Any]:
    fn = _PROBES.get(str(platform).lower())
    if not fn:
        # 未知平台不拦（新平台由发布侧自己 fail-closed），但要留痕
        return {"platform": platform, "ok": True, "code": "NO_PROBE",
                "detail": "该平台无前置探活（未接入 preflight）", "fix": ""}
    return fn()


def probe_all(platforms: List[str]) -> List[Dict[str, Any]]:
    return [probe_platform(p) for p in platforms]


def alert_lines(fails: List[Dict[str, Any]]) -> List[str]:
    """告警卡正文：每个失败平台一行原因 + 一行可直接粘贴的修复命令。"""
    lines: List[str] = []
    for f in fails:
        lines.append(f"**❌ {f['platform']}**（{f['code']}）：{f['detail']}")
        if f.get("fix"):
            lines.append(f"```\n{f['fix']}\n```")
    lines.append("已**不发布任何内容**：修好登录态后重新触发发布即可（台账未被占用，不产生脏 claim）")
    return lines


def send_alert(fails: List[Dict[str, Any]], run_id: str, title: str = "") -> str:
    """发前置预检告警卡。发送失败只打印，不掩盖真实失败原因。"""
    from src.feishu.approval_service import FeishuApprovalService
    from src.feishu.card_builder import FeishuCardBuilder

    chat = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not chat:
        print("  ! 前置告警卡跳过：未配置 FEISHU_APPROVAL_CHAT_ID")
        return ""
    id_type = "chat_id" if chat.startswith("oc_") else "open_id"
    card = FeishuCardBuilder.build_status_card(
        run_id=run_id,
        title=title,
        desc=f"以下平台前置检查未通过：{'、'.join(f['platform'] for f in fails)}",
        header_text="🚫 发布前置预检未通过",
        header_template="red",
        lines=alert_lines(fails),
    )
    msg_id = FeishuApprovalService().send_approval_card(
        receive_id=chat, receive_id_type=id_type, card_content=card)
    print(f"  ✓ 前置告警卡已发（msg_id={msg_id}）")
    return str(msg_id or "")
