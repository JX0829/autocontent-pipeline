#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音发布页表单探针：只填表、不发布。

用途（A1/A6 与 F1 迭代用）：
  · `--cover`   上传自定义封面并回读 dHash 自证，打印结论 + 保存整页截图；
  · `--keep`    保留页面（默认关掉 tab，不点发布、不保存草稿）。

用法：
  python3 scripts/probe_douyin_form.py --cover output/20261118/openai-chile-1/cover.png
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
EDITOR = 'div[contenteditable="true"]'


def _make_video(path: Path) -> Path:
    if path.exists():
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x101820:s=1080x1920:d=3", "-r", "30", "-pix_fmt", "yuv420p",
         str(path)], check=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9333")
    ap.add_argument("--video", default="", help="用真实成片（否则造 3 秒空白片）")
    ap.add_argument("--cover", default="")
    ap.add_argument("--shot", default="/tmp/douyin_cover_form.png")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    from src.providers import douyin_form

    with tempfile.TemporaryDirectory() as td:
        video = Path(a.video) if a.video else _make_video(Path(td) / "probe.mp4")
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(a.cdp_url, timeout=15000)
            ctx = browser.contexts[0]
            page = ctx.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if "login" in page.url.lower():
                print("❌ 抖音未登录", page.url)
                return 2
            page.locator('input[type="file"]').first.set_input_files(str(video))
            page.wait_for_selector(EDITOR, timeout=90000)
            page.wait_for_timeout(4000)

            out = {"cover": None}
            if a.cover:
                out["cover"] = douyin_form.set_cover(page, a.cover)
            page.screenshot(path=a.shot, full_page=True)
            out["shot"] = a.shot
            print(json.dumps(out, ensure_ascii=False, indent=2))
            if not a.keep:
                page.close()
            return 0 if (not a.cover or (out["cover"] or {}).get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
