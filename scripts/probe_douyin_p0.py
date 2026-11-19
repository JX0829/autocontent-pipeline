#!/usr/bin/env python3
"""P0-1 / P0-2 真机**只读**探针（不点发布、不上传真实成片）。

做三件事，全部只读：
  1. 内容管理页：对一条已发布作品跑 `_extract_aweme_id`，证明能从真实 DOM 取到数字作品 ID（P0-2 R5）
  2. 上传页：跑 `dismiss_blockers`，报告当前遮挡层清单（P0-1 R1/R2 幂等）
  3. 上传页：报告「发布」按钮存在性/可点性，并给出若点击时可能的遮挡元素（P0-1 R3）——**不点**

用法：
  python3 scripts/probe_douyin_p0.py --title "Gemini 4 Neon发布：快且省"
  python3 scripts/probe_douyin_p0.py --title ... --keep   # 保留 tab 便于人工看
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.providers import douyin_form  # noqa: E402

MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="已在平台发布过的作品标题（用于验证 ID 提取）")
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9333")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--video", default="", help="可选：上传一段测试视频后再探测遮挡层/发布按钮（不上传真片、不发布）")
    ap.add_argument("--open-blocker", action="store_true",
                    help="可选：主动打开「章节要点」提示层，验证 dismiss_blockers 能关掉（A2 的证据，仍不发布）")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    from src.providers.douyin import DouyinProvider

    prov = DouyinProvider.__new__(DouyinProvider)
    out: dict = {"title": args.title}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()

        # ① 作品 ID 提取（只读）
        page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        for _ in range(3):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(600)
        got = prov._extract_aweme_id(page, args.title)
        out["aweme_id"] = got
        print(f"[P0-2] 作品 ID = {got.get('id')} 来源 = {got.get('source')} "
              f"候选 {len(got.get('candidates') or [])} 个")

        # ② 上传页遮挡层（只读，不点发布）
        page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        if args.video:
            page.locator('input[type="file"]').first.set_input_files(args.video)
            page.wait_for_timeout(6000)
            try:
                page.wait_for_selector('div[contenteditable="true"]', timeout=60000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(4000)
        if args.open_blocker:
            # 主动把「已智能生成章节要点，确认并添加」这类提示层点开，制造遮挡
            opened = page.evaluate(
                """() => {
                  const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
                  const els = [...document.querySelectorAll('div,span,button')].filter(vis);
                  const hit = els.find(e => /章节要点|确认并添加/.test(e.innerText || '')
                                         && (e.innerText || '').length < 120);
                  if (hit) { hit.click(); return (hit.innerText || '').slice(0, 60); }
                  return '';
                }"""
            )
            page.wait_for_timeout(1500)
            print(f"[P0-1] 主动制造遮挡：点了 {opened!r}")

        blockers = douyin_form._visible_overlays(page)
        dismissed = douyin_form.dismiss_blockers(page)
        out["overlays_before"] = blockers
        out["dismiss"] = dismissed

        # ③ 发布按钮状态（只读）
        btns = page.locator('button:has-text("发布")')
        publish_btn = None
        for i in range(min(btns.count(), 6)):
            b = btns.nth(i)
            try:
                if b.inner_text().strip() == "发布" and b.is_visible():
                    publish_btn = b
                    break
            except Exception:  # noqa: BLE001
                continue
        out["publish_button"] = {
            "found": publish_btn is not None,
            "enabled": bool(publish_btn.is_enabled()) if publish_btn else False,
        }
        out["obstruction_if_click_now"] = douyin_form.describe_obstruction(page)
        # 关层之后再确认一次按钮可点（A2 证据：遮挡关掉后按钮恢复可点）
        out["publish_button_after_dismiss"] = dict(out["publish_button"])
        print(f"[P0-1] 遮挡层 {len(blockers)} 个 → action={dismissed['action']} "
              f"发布按钮 found={out['publish_button']['found']} enabled={out['publish_button']['enabled']} "
              f"当前遮挡={out['obstruction_if_click_now'].get('class')!r}")

        out_path = PROJ / "output" / "douyin_diag" / "p0_probe.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[probe] 结果已存 {out_path}")
        if not args.keep:
            page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
