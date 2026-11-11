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


def _seed_comment_plan(account: dict, draft: dict) -> tuple[bool, bool, str]:
    """作者首评：配置在 accounts.yaml 的 seed_comment 块。抖音需 CDP UI 实拍验证后才能开启。"""
    try:
        sys.path.insert(0, "/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
        from src.utils.seed_comment import resolve
        return resolve(account, draft, platform="douyin")
    except Exception as exc:  # noqa: BLE001
        _log(f"SEED_COMMENT_CFG_FAILED: {exc}")
        return False, False, ""


def publish(video: Path, title: str, summary: str, tags: list[str],
            cdp_url: str, dry_run: bool, account: dict) -> int:
    from playwright.sync_api import sync_playwright

    caption = f"{title} {_normalize_tags(tags)}\n{summary}".strip()
    if dry_run:
        _log(f"[dry-run] video={video} exists={video.exists()} size={video.stat().st_size if video.exists() else 0}")
        _log(f"[dry-run] title={title!r} tags={len(tags)} caption_len={len(caption)}")
        _log(f"[dry-run] account={account.get('id')} publish_mode={account.get('publish_mode')}")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = _open_page(ctx)

        before_text, before_line = _read_works(page)
        _log(f"CDP ok | BEFORE {before_line or '未识别'} | title_present={title in before_text} | url={page.url}")
        if dry_run:
            _log("[dry-run] 输入与 CDP 会话均正常，未上传未发布")
            return 0

        if title in before_text:
            _log("该标题已在作品列表中，视为已发布，跳过（幂等）")
            return 0

        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
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
    args = ap.parse_args()

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
        rc = publish(video, title, summary, tags, args.cdp_url, args.dry_run, account)
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
