#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""脚本化补标题：把已发布但「空标题/无作品描述」的抖音作品补上标题。

背景（2026-11-15）：CDP 通道两次成功、一次空标题，说明「填标题」这一步本身不稳定。
现在发布侧已经 fail-closed（点发布前自证），但**已经产出**的空标题作品还得能自动补救，
不能靠人点后台。

用法：
    # 1) 只定位、只读（默认 dry-run）：告诉你在第几个卡片、当前标题是什么
    python3 scripts/fix_douyin_title.py --date "2026-11-15 17:57" --title "本地AI省账单？云端派表示不服"

    # 2) 真的补上
    python3 scripts/fix_douyin_title.py --date "2026-11-15 17:57" --title "..." --apply

    # 3) 从 run 目录自动取标题（output/<日期>/<run_id>/manifest.json 或 publish.json）
    python3 scripts/fix_douyin_title.py --run <run_id> --date "2026-11-15 17:57" --apply

安全线：
  · 默认 dry-run，只有 --apply 才写；
  · 只改标题，不动视频/描述/可见性；
  · 找不到目标卡片就退出码 3，绝不猜着改。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

DEFAULT_CDP = "http://127.0.0.1:9333"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _title_from_run(run_id: str) -> str:
    """从 run 目录里捞标题（draft_script.json / manifest.json）。"""
    for d in sorted((PROJ / "output").glob(f"*/{run_id}"), reverse=True):
        for name in ("draft_script.json", "manifest.json"):
            f = d / name
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            t = str((data.get("draft") or data).get("title") or data.get("title") or "").strip()
            if t:
                return t
    return ""


def _cards(page) -> list[dict]:
    """抓内容管理页的作品行：日期 + 可见文本。按页面顺序（最新在前）。"""
    lines = [l.strip() for l in page.inner_text("body").split("\n") if l.strip()]
    cards, cur = [], []
    for l in lines:
        cur.append(l)
        if re.match(r"^\d{4}年\d{2}月\d{2}日", l):
            text = " ".join(cur)
            cards.append({"date": l, "text": text[:300]})
            cur = []
    return cards


def _find_target(page, date_hint: str, caption_hint: str) -> dict | None:
    want = re.sub(r"[-:/]", "年" if "-" in date_hint else "", date_hint)  # 占位，下面用宽松匹配
    for c in _cards(page):
        if date_hint and date_hint not in c["date"] and c["date"] not in date_hint:
            continue
        if caption_hint and caption_hint not in c["text"]:
            continue
        return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="给抖音已发布作品补标题（默认 dry-run）")
    ap.add_argument("--date", required=True, help='作品发布日期，如 "2026年11月15日 17:57" 或 "11月15日 17:57"')
    ap.add_argument("--title", help="要补的标题（缺省用 --run 里读到的）")
    ap.add_argument("--run", help="run_id：从 output/<日期>/<run_id>/ 里读标题")
    ap.add_argument("--caption", default="", help="正文片段，用于在卡片里二次确认")
    ap.add_argument("--cdp-url", default=DEFAULT_CDP)
    ap.add_argument("--apply", action="store_true", help="真的写入（默认只读）")
    a = ap.parse_args()

    title = (a.title or _title_from_run(a.run or "") or "").strip()[:30]
    if not title:
        _log("✗ 没拿到标题：请给 --title 或 --run")
        return 2
    _log(f"目标标题: {title!r} | 日期: {a.date} | 模式: {'APPLY' if a.apply else 'DRY-RUN'}")

    from playwright.sync_api import sync_playwright
    from src.providers import cdp_session, douyin_form

    with sync_playwright() as pw:
        ctx, browser = cdp_session.connect(pw, a.cdp_url)
        page = ctx.new_page()
        page.goto(MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(9000)
        for _ in range(3):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(600)

        cards = _cards(page)
        _log(f"内容管理页识别到 {len(cards)} 个作品卡片（最新在前）")
        target = _find_target(page, a.date, a.caption)
        if not target:
            _log("✗ 没找到匹配该日期/正文的作品卡片 —— 不动手（退出码 3）")
            _log("  提示：把 --date 换成页面上显示的原样文本，或加 --caption 精确一点")
            browser.close()
            return 3
        _log(f"✓ 命中卡片: {target['date']} | {target['text'][:100]}")

        # 打开该作品的编辑面板 —— 抖音卡片右上角有「编辑作品」
        opened = False
        try:
            card_loc = page.locator("div", has_text=target["date"]).last
            edit = page.locator('text=编辑作品').first
            if edit.count() > 0:
                edit.click(force=True)
                page.wait_for_timeout(4000)
                opened = True
        except Exception as exc:  # noqa: BLE001
            _log(f"打开编辑面板异常: {exc}")

        if not opened:
            _log("✗ 没能打开编辑面板（UI 结构变了？）—— 不动手")
            browser.close()
            return 3

        cur_title = douyin_form.title_value(page)
        _log(f"编辑页当前标题框值: {cur_title!r}")
        if not a.apply:
            _log("[dry-run] 到此为止，没写入。加 --apply 才真的补标题。")
            browser.close()
            return 0

        ok, landed, err = douyin_form.fill_title(page, title)
        if not ok:
            _log(f"✗ 标题填入失败: {err}")
            browser.close()
            return 2
        _log(f"✓ 已填入标题: {landed!r}")

        saved = False
        for label in ("保存", "发布", "确定"):
            btn = page.locator(f'button:has-text("{label}")').first
            try:
                if btn.count() > 0 and btn.is_visible():
                    btn.click()
                    saved = True
                    _log(f"已点击「{label}」保存")
                    break
            except Exception:  # noqa: BLE001
                continue
        page.wait_for_timeout(4000)
        browser.close()
        if not saved:
            _log("⚠️ 没找到保存按钮，标题可能只留在编辑页 —— 请人工确认")
            return 4
        _log("✅ 补标题流程完成，请到后台确认该作品标题已显示")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
