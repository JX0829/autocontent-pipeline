#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号发布页结构探针：上传后把「封面」相关入口/按钮/文件框全部打出来。

只读不发布。用法：python3 scripts/probe_channels_cover.py --video <mp4>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
POST_URL = "https://channels.weixin.qq.com/platform/post/create"

DUMP = r"""() => {
  const out = {texts: [], buttons: [], inputs: [], imgs: []};
  const walk = (doc) => {
    for (const el of doc.querySelectorAll('button, [role=button], span, div')) {
      const t = (el.innerText || '').trim();
      if (!t || t.length > 24) continue;
      if (!t.includes('封面')) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      out.texts.push({t, tag: el.tagName, cls: (el.className || '').toString().slice(0, 60),
                      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
    }
    for (const el of doc.querySelectorAll('input[type=file]')) {
      out.inputs.push({accept: el.accept || '', cls: (el.className || '').toString().slice(0, 60)});
    }
    for (const el of doc.querySelectorAll('button')) {
      const t = (el.innerText || '').trim();
      if (t) out.buttons.push(t.slice(0, 16));
    }
    for (const el of doc.querySelectorAll('img')) {
      const r = el.getBoundingClientRect();
      if (r.width >= 40) out.imgs.push({src: (el.currentSrc || el.src || '').slice(0, 30),
                                        box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)]});
    }
  };
  walk(document);
  for (const f of document.querySelectorAll('iframe')) {
    try { walk(f.contentDocument); } catch (e) {}
  }
  return {texts: out.texts.slice(0, 14), buttons: [...new Set(out.buttons)].slice(0, 20),
          inputs: out.inputs.slice(0, 8), imgs: out.imgs.slice(0, 10)};
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9334")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(a.cdp_url, timeout=20000)
        page = b.contexts[0].new_page()
        page.goto(POST_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        print("url:", page.url)
        try:
            page.locator('input[type="file"]').first.set_input_files(a.video)
            print("已投喂视频，等转码…")
        except Exception as exc:  # noqa: BLE001
            print("上传失败:", exc)
        page.wait_for_timeout(20000)
        out = page.evaluate(DUMP)
        print(json.dumps(out, ensure_ascii=False, indent=2)[:4000])
        page.screenshot(path="/tmp/channels_form.png", full_page=True)
        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
