#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R4 真机 A/B：抖音 ACE 编辑器到底吃什么分段（`\\n` / `\\n\\n` / `<div>` / 段落回车）。

**只填表、不发布**：上传一段 3 秒测试视频到草稿页，依次用三种写法填描述，
每次读回编辑器真实 DOM 结构，最后关掉这个 tab（不点发布、不保存）。

用法：
  python3 scripts/probe_douyin_desc_ab.py --cdp-url http://127.0.0.1:9333 [--keep]

输出：一段 JSON + 人读表，结论用 data 说话（段数 / innerText 换行 / 节点结构）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
EDITOR = 'div[contenteditable="true"]'

#: 三种候选写法（正文都用同一段三句文案，只改分隔方式）
SEGMENTS = ["第一段：抖音分段测试甲。", "第二段：抖音分段测试乙。", "第三段：抖音分段测试丙。"]
MODES = {
    "single_nl": "\n".join(SEGMENTS),
    "double_nl": "\n\n".join(SEGMENTS),
    "html_div": "".join(f"<div>{s}</div>" for s in SEGMENTS),
    "triple_nl": "\n\n\n".join(SEGMENTS),
}

READBACK_JS = r"""
() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return {ok: false, reason: 'no_editor'};
  const ZW = /[\u200b\u200c\u200d\ufeff]/g;
  const txt = (ed.innerText || '').replace(ZW, '');
  const kids = [...ed.children];
  const tags = {};
  kids.forEach(k => { tags[k.tagName] = (tags[k.tagName] || 0) + 1; });
  const blocks = [...ed.querySelectorAll('div,p')].filter(e => (e.innerText || '').trim());
  return {
    ok: true,
    newlines: (txt.match(/\n/g) || []).length,
    outerNewlines: (txt.match(/\n+/g) || []).length,
    blockCount: blocks.length,
    childCount: kids.length,
    childTags: tags,
    aceLineCount: ed.querySelectorAll('.ace-line').length,
    htmlHead: (ed.innerHTML || '').slice(0, 220),
    textHead: txt.slice(0, 80).replace(/\n/g, '\\n'),
  };
}
"""


def _make_video(path: Path) -> Path:
    if path.exists():
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x101820:s=1080x1920:d=3",   # 只测描述框，不需要画面文字
         "-r", "30", "-pix_fmt", "yuv420p", str(path)],
        check=True)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9333")
    ap.add_argument("--video", default="")
    ap.add_argument("--keep", action="store_true", help="通过后保留页面（默认关闭）")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory() as td:
        video = Path(a.video) if a.video else _make_video(Path(td) / "r4_ab.mp4")

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(a.cdp_url, timeout=15000)
            ctx = browser.contexts[0]
            page = ctx.new_page()
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if "login" in page.url.lower():
                print("❌ 抖音未登录，先扫码", page.url)
                return 2

            # 上传测试视频（草稿，不发布会）
            up = page.locator('input[type="file"]').first
            up.set_input_files(str(video))
            page.wait_for_selector(EDITOR, timeout=90000)
            page.wait_for_timeout(3000)
            editor = page.locator(EDITOR).first

            def clear() -> None:
                editor.click()
                page.keyboard.press("Meta+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(400)

            def type_native(text: str) -> None:
                """走真实事件链：insert_text + 每段之间回车（和线上 native 策略一致）。"""
                parts = text.split("\n")
                for i, seg in enumerate(parts):
                    if i:
                        page.keyboard.press("Enter")
                    page.keyboard.insert_text(seg)
                page.wait_for_timeout(600)

            def js_insert(text: str) -> None:
                page.evaluate(
                    """(t) => {
                        const ed = document.querySelector('div[contenteditable="true"]');
                        ed.focus();
                        const html = t.includes('<div>') ? t : t.split('\\n').map(s => `<div>${s}</div>`).join('');
                        document.execCommand('insertHTML', false, html);
                    }""", text)
                page.wait_for_timeout(600)

            results = {}
            for name, text in MODES.items():
                for strategy in ("nl_native", "html_exec"):
                    clear()
                    if strategy == "nl_native" and "<div>" in text:
                        continue          # 原生按键没法输入 HTML
                    if strategy == "nl_native":
                        type_native(text)
                    else:
                        js_insert(text)
                    results[f"{name}/{strategy}"] = page.evaluate(READBACK_JS)

            print(json.dumps({"video": str(video), "results": results},
                             ensure_ascii=False, indent=2))

            # 汇总：哪种写法真的保住了 3 段
            print("\n=== 汇总（want 3 段）===")
            print(f"{'写法':<22}{'换行':>5}{'块数':>5}{'子节点':>6}{'.ace-line':>10}  结论")
            for k, v in results.items():
                seg_ok = v.get("blockCount", 0) >= 3 or v.get("outerNewlines", 0) >= 2
                print(f"{k:<22}{v.get('newlines', 0):>5}{v.get('blockCount', 0):>5}"
                      f"{v.get('childCount', 0):>6}{v.get('aceLineCount', 0):>10}  "
                      f"{'✅ 保留分段' if seg_ok else '❌ 被压平'}")

            clear()
            if not a.keep:
                page.close()          # 关 tab，不点发布
            return 0


if __name__ == "__main__":
    sys.exit(main())
