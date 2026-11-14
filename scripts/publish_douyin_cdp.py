#!/usr/bin/env python3
"""抖音 CDP 真机发布器（RUNBOOK §5.2）

背景：headless Playwright 会被抖音风控识别（扫码/短信验证必失败），
因此本脚本用 connect_over_cdp 接管**已人工登录的真实 Chrome**，复用其会话发布。

前置：
  1. 真实 Chrome 以调试端口启动（默认 9333，勿用 9222——该端口专供 Agent-Reach）：
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
       --remote-debugging-port=9333 \
       --user-data-dir=<项目>/data/browser_profiles/douyin-cdp \
       --no-first-run --no-default-browser-check --window-size=1280,900 about:blank
  2. 在该窗口内人工扫码/验证码登录创作者中心（一次即可，会话持久化在 user-data-dir）

用法：
  # 自检（只校验输入 + CDP 连通 + 回读作品数，不上传不发布）
  python3 scripts/publish_douyin_cdp.py --run-id codex-harness-1 --dry-run

  # 真实发布（私密作品，仅自己可见）+ 回读校验
  python3 scripts/publish_douyin_cdp.py --run-id codex-harness-1

  # 直接给素材
  python3 scripts/publish_douyin_cdp.py --video output/x/video.mp4 \
      --title "标题" --summary "正文" --tags 标签1 标签2

退出码：0=已入库并回读命中；2=入参/环境错误；6=发布后回读未命中；7=需要人工二次验证。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.utils.accounts import for_platform  # noqa: E402

LOG = logging.getLogger("publish_douyin_cdp")

CDP_URL_DEFAULT = "http://127.0.0.1:9333"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
DIAG_DIR = PROJ / "output" / "douyin_diag"


def _log(msg: str) -> None:
    LOG.info(msg)


def _release_claim(key: str, reason: str) -> None:
    """前置阶段失败（确定没提交）→ 标 failed 释放占位，下次可重试。

    VALIDATION 在 `SAFE_RETRY_SIGNALS` 里，`record_outcome` 会把它落成 failed，
    而 failed/deleted 是允许重新 claim 的状态。
    """
    from src.publish_ledger import record_outcome
    state = record_outcome(key, False, status="VALIDATION", reason=reason[:200])
    _log(f"台账: {state}（前置失败已释放 claim）")


def _normalize_tags(tags: list[str]) -> str:
    return " ".join(t if str(t).startswith("#") else f"#{t}" for t in tags if str(t).strip())


def _load_from_run(run_id: str) -> tuple[Path, str, str, list[str]]:
    """从 output/<YYYYMMDD>/<run_id>/ 读取产物与文案。"""
    candidates = sorted(PROJ.glob(f"output/*/{run_id}/video.mp4"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"找不到 run '{run_id}' 的 video.mp4（output/*/{run_id}/）")
    video = candidates[-1]
    script_path = video.parent / "draft_script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"缺少 draft_script.json: {script_path}")
    script = json.loads(script_path.read_text(encoding="utf-8"))
    title = str(script.get("title") or "").strip()
    # 简介统一走 src/utils/video_desc（人话、无斜杠命令；老 run 现生成并缓存）
    from src.utils.video_desc import ensure_desc
    summary = ensure_desc(script_path.parent, script)
    tags = list(script.get("tags") or script.get("topics") or [])
    if not title:
        raise ValueError(f"{script_path} 缺少 title")
    return video, title, summary, tags


def _read_works(page) -> tuple[str, str]:
    """回读作品列表页，返回 (正文, '作品 (N)' 行)。"""
    page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(6)
    text = page.inner_text("body")
    line = next((ln for ln in text.split("\n") if "作品 (" in ln), "")
    return text, line


def _open_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


class PublishPreflightError(RuntimeError):
    """发布**前置**阶段失败（CDP 连不上 / 上传页没准备好 / 没点到「发布」）。

    这一阶段失败 == 平台侧确定没收到内容，可以安全释放台账 claim 并重试；
    点过「发布」之后的异常一律按 SUBMIT_UNKNOWN 处理（禁止盲目重发）。
    """


CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def chrome_launch_hint(cdp_url: str) -> str:
    """掉线时的可执行指引（照抄 RUNBOOK §5.2 的启动命令）。"""
    port = cdp_url.rsplit(":", 1)[-1].strip("/") or "9333"
    return (f'"{CHROME_BIN}" --remote-debugging-port={port} '
            f'--user-data-dir={PROJ}/data/browser_profiles/douyin-cdp '
            f'--no-first-run --no-default-browser-check --window-size=1280,900 about:blank')


def _probe_cdp(cdp_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """`GET /json/version` 探活（3 秒超时）。

    原先直接 `connect_over_cdp`：掉线时抛的是 playwright 的 ECONNREFUSED 长栈，
    而且**已经占了台账 claim**，于是下一次发布被自己拦住。探活必须在 claim 之前。
    """
    import urllib.error
    import urllib.request

    url = f"{cdp_url.rstrip('/')}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
        data = json.loads(raw)
        browser = str(data.get("Browser") or "?")
        return True, browser
    except urllib.error.URLError as exc:
        return False, f"{type(exc).__name__}: {exc.reason}"
    except Exception as exc:  # noqa: BLE001 — 探活失败原因必须原样回给用户
        return False, f"{type(exc).__name__}: {exc}"


def _seed_comment_plan(account: dict, draft: dict) -> tuple[bool, bool, str]:
    """作者首评：配置在 accounts.yaml 的 seed_comment 块。抖音需 CDP UI 实拍验证后才能开启。"""
    try:
        sys.path.insert(0, "/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
        from src.utils.seed_comment import resolve
        return resolve(account, draft, platform="douyin")
    except Exception as exc:  # noqa: BLE001
        _log(f"SEED_COMMENT_CFG_FAILED: {exc}")
        return False, False, ""


RESUME_MODAL_MARKERS = ("上次未发布", "是否继续编辑", "继续编辑上次", "还有未发布的视频")


def dismiss_resume_modal(page, timeout: float = 8.0) -> str:
    """处理「你还有上次未发布的视频，是否继续编辑？」弹窗，一律选**放弃**。

    2026-11-14 实测：这个弹窗挡在上传页时，脚本会一直等 `div[contenteditable="true"]`
    直到 30s 超时 EXIT=2（使用者手动点「放弃」后重跑才通）。
    为什么不选「继续编辑」：我们每次都是整条新片重传，续上旧草稿会挂错视频/旧标题。
    返回 'none' / 'dismissed' / 'escape'，只用于日志。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            body = page.inner_text("body")[:4000]
        except Exception:  # noqa: BLE001 — 页面切换中取不到正文，下一轮再试
            body = ""
        if any(m in body for m in RESUME_MODAL_MARKERS):
            for sel in ('button:has-text("放弃")', 'button:has-text("不继续")',
                        'button:has-text("重新上传")',
                        'div[role="dialog"] button:has-text("放弃")',
                        'div[class*="modal"] button:has-text("放弃")'):
                loc = page.locator(sel).first
                try:
                    if loc.count() > 0:
                        loc.click(timeout=3000)
                        time.sleep(2)
                        _log(f"检测到「上次未发布视频」弹窗 → 已放弃旧草稿（{sel}）")
                        return "dismissed"
                except Exception:  # noqa: BLE001 — 换下一个候选选择器
                    continue
            _log("检测到「上次未发布视频」弹窗，但没找到「放弃」按钮 → 按 Esc 关掉")
            try:
                page.keyboard.press("Escape")
                time.sleep(1)
            except Exception:  # noqa: BLE001
                pass
            return "escape"
        time.sleep(0.5)
    return "none"


def publish(video: Path, title: str, summary: str, tags: list[str],
            cdp_url: str, dry_run: bool, account: dict) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise PublishPreflightError(f"缺少 playwright（当前解释器 {sys.executable}）: {exc.name}") from exc

    caption = f"{title} {_normalize_tags(tags)}\n{summary}".strip()
    if dry_run:
        _log(f"[dry-run] video={video} exists={video.exists()} size={video.stat().st_size if video.exists() else 0}")
        _log(f"[dry-run] title={title!r} tags={len(tags)} caption_len={len(caption)}")
        _log(f"[dry-run] account={account.get('id')} publish_mode={account.get('publish_mode')}")

    with sync_playwright() as pw:
        # ── 前置阶段：连会话 + 读管理页。失败时平台侧一定没收到内容。
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = _open_page(ctx)

            before_text, before_line = _read_works(page)
        except Exception as exc:  # noqa: BLE001 — 统一转成可释放 claim 的前置错误
            raise PublishPreflightError(f"CDP 会话不可用（{cdp_url}）: {exc}") from exc
        _log(f"CDP ok | BEFORE {before_line or '未识别'} | title_present={title in before_text} | url={page.url}")
        if dry_run:
            _log("[dry-run] 输入与 CDP 会话均正常，未上传未发布")
            return 0

        if title in before_text:
            _log("该标题已在作品列表中，视为已发布，跳过（幂等）")
            return 0

        # ── 仍未点「发布」：上传页准备阶段的失败同样可安全重试
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            dismiss_resume_modal(page)
            page.locator('input[type="file"]').first.set_input_files(str(video))
            page.wait_for_selector('div[contenteditable="true"]', timeout=90000)
            time.sleep(3)
            editor = page.locator('div[contenteditable="true"]').first
            editor.click()
            editor.fill("")
            editor.type(caption, delay=10)
            time.sleep(2)

            for _ in range(40):
                if "上传成功" in page.inner_text("body"):
                    break
                time.sleep(3)

            if str(account.get("publish_mode") or "") == "private_publish":
                label = page.locator('xpath=//label[contains(., "仅自己可见")]').first
                if label.count():
                    label.click()
                    time.sleep(1)
                    _log("已勾选「仅自己可见」")

            clicked = False
            for i in range(page.locator('button:has-text("发布")').count()):
                btn = page.locator('button:has-text("发布")').nth(i)
                try:
                    if btn.inner_text().strip() == "发布" and btn.is_visible() and btn.is_enabled():
                        btn.click()
                        clicked = True
                        break
                except Exception as exc:  # noqa: BLE001 - 单个按钮不可点不应中断整轮查找
                    LOG.debug(f"跳过不可点按钮 #{i}: {exc}")
            if not clicked:
                _log("未找到可点击的「发布」按钮")
                return 2
        except Exception as exc:  # noqa: BLE001 — 未点发布 ⇒ 不是「可能已提交」
            raise PublishPreflightError(f"上传页准备失败（未点「发布」）: {exc}") from exc

        _log("已点击「发布」")
        time.sleep(6)

        if page.locator('input[placeholder*="验证码"]').count() > 0:
            DIAG_DIR.mkdir(parents=True, exist_ok=True)
            shot = DIAG_DIR / f"need_verify_{int(time.time())}.png"
            page.screenshot(path=str(shot), full_page=True)
            _log(f"NEED_VERIFY：出现二次验证，需人工处理。截图 {shot}")
            return 7

        for _ in range(20):
            time.sleep(5)
            if "content/manage" in page.url:
                _log("发布后已跳回内容管理页")
                break

        for attempt in range(8):
            text, line = _read_works(page)
            if title in text:
                idx = text.find(title)
                _log(f"VERIFIED_OK {line} | {text[max(0, idx - 80):idx + 120]!r}")
                return 0
            _log(f"readback{attempt + 1} 未命中（{line or '未识别'}）")
            time.sleep(6)

        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        shot = DIAG_DIR / f"publish_unverified_{int(time.time())}.png"
        page.screenshot(path=str(shot), full_page=True)
        _log(f"回读未命中标题，判定发布失败。截图 {shot}")
        return 6


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="抖音 CDP 真机发布器（RUNBOOK §5.2）")
    ap.add_argument("--run-id", help="从 output/*/<run_id>/ 读取 video.mp4 + draft_script.json")
    ap.add_argument("--video", help="直接指定竖版视频路径")
    ap.add_argument("--title", help="标题（配合 --video 使用）")
    ap.add_argument("--summary", default="", help="正文摘要")
    ap.add_argument("--tags", nargs="*", default=[], help="话题标签（可带或不带 #）")
    ap.add_argument("--cdp-url", default=os.getenv("DOUYIN_CDP_URL", CDP_URL_DEFAULT))
    ap.add_argument("--dry-run", action="store_true", help="只校验输入与 CDP 连通，不上传不发布")
    ap.add_argument("--force", action="store_true",
                    help="确认要走单平台入口（默认拒绝，统一用 runner --publish）")
    args = ap.parse_args()

    # 单平台入口已收口到流水线：默认拒绝，避免绕过审批/台账/回执卡三条链路
    if not (args.force or args.dry_run):
        _log("⚠️  单平台发布入口已废弃，统一走流水线一条命令：")
        print("   python3 -m src.pipeline.runner --material materials/<物料>.json \\")
        print("           --publish douyin,bilibili,channels")
        print("   排障确实需要单平台时加 --force 重跑（--dry-run 不受影响）。")
        return 2

    try:
        account = for_platform("douyin")
    except Exception as exc:  # noqa: BLE001 - 配置缺失需明确告知
        _log(f"读取抖音账号配置失败: {exc}")
        return 2

    try:
        if args.run_id:
            video, title, summary, tags = _load_from_run(args.run_id)
            draft_path = video.parent / "draft_script.json"
            draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
        elif args.video and args.title:
            video, title = Path(args.video), args.title
            summary, tags = args.summary, args.tags
            draft = {}
        else:
            _log("必须提供 --run-id，或 --video 与 --title")
            return 2
        if not video.exists():
            _log(f"视频不存在: {video}")
            return 2
        # CDP 探活必须在**占台账之前**：掉线时留下 claim 会让之后每次发布都被自己拦住
        ok, detail = _probe_cdp(args.cdp_url)
        if not ok:
            _log(f"⛔ CDP 不可用（{args.cdp_url}）：{detail}")
            _log(f"  先启动已登录的 Chrome（RUNBOOK §5.2）：")
            _log(f"  {chrome_launch_hint(args.cdp_url)}")
            return 2
        _log(f"CDP 探活 ok（{args.cdp_url}）: {detail}")
        # 端口级互斥（R5，2026-11-14 补）：provider 侧早就有，CLI 这半边一直缺，
        # 于是「流水线在发 + 人手动 CLI 补发」会抢同一个 tab（模板：ds-coding-plan-1
        # 那次 contenteditable 点击超时，很可能就是并发抢焦点导致）。
        # CLI 是短命进程，锁持有到进程退出即可（flock 随进程自动释放）。
        cdp_session = None
        try:
            from src.providers import cdp_session
            _port_lock = cdp_session.exclusive("douyin", args.cdp_url)
            _port_lock.__enter__()
        except Exception as exc:  # noqa: BLE001
            if cdp_session is not None and isinstance(exc, cdp_session.CdpBusy):
                _log(f"⛔ {exc}")
                return 2
            _log(f"[warn] 端口互斥锁不可用，继续发布：{type(exc).__name__}: {exc}")
        seed_on, seed_pin, _ = _seed_comment_plan(account, draft)
        if seed_on:
            _log(f"seed_comment: 已配置(置顶={seed_pin})，抖音 CDP 首评链路待实拍验证后启用")
        # 发布前查重（dry-run 不查重，避免占位把真实发布挡死）
        led_key = ""
        if not args.dry_run:
            from src.publish_ledger import PublishBlocked, check_and_claim
            try:
                led_key, _info = check_and_claim("douyin", str(account.get("id") or "douyin"),
                                                 video, title)
            except PublishBlocked as exc:
                _log(f"⛔ 发布前查重拦截：{exc}")
                return 3
        rc = None
        try:
            rc = publish(video, title, summary, tags, args.cdp_url, args.dry_run, account)
        except PublishPreflightError as exc:
            # 没点「发布」⇒ 平台确定没收到：释放 claim，允许重试
            _log(f"⛔ 发布前置失败（未点「发布」，不占用台账）：{exc}")
            if led_key:
                _release_claim(led_key, f"preflight: {exc}")
            return 2
        except Exception as exc:  # noqa: BLE001 — 点过发布后的未知异常：可能已提交，禁止重发
            _log(f"⚠ 发布中断（可能已提交，禁止盲目重发）：{type(exc).__name__}: {exc}")
            if led_key:
                from src.publish_ledger import record_outcome
                state = record_outcome(led_key, False, status="SUBMIT_UNKNOWN",
                                       reason=f"{type(exc).__name__}: {exc}"[:200])
                _log(f"台账: {state}")
            return 1
        if led_key:
            from src.publish_ledger import record_outcome
            # rc=2 是参数/校验类失败（还没进发布流程）→ 可重试；rc=1 可能点了发布但没确认 → 禁止重发
            if rc == 0:
                state = record_outcome(led_key, True)
            elif rc == 2:
                state = record_outcome(led_key, False, status="VALIDATION", reason=f"publish rc={rc}")
            else:
                state = record_outcome(led_key, False, status="SUBMIT_UNKNOWN", reason=f"publish rc={rc}")
            _log(f"台账: {state}")
        return rc
    except FileNotFoundError as exc:
        _log(str(exc))
        return 2
    except ValueError as exc:
        _log(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
