#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号「话题真生效」契约测试（不需要浏览器）。

防的是什么：2026-11-15 发现视频号话题一直没生效 —— 我们把 `#话题` 当纯文本
`execCommand('insertText')` 一次粘进描述框，平台不会把它变成话题（post_list
回读 `desc.topic = false`）。实测只有**逐字符敲入** `#关键词` 时，编辑器才会包成
`<span class="hl topic" data-type="topic">`。

所以这里把「必须怎么做」钉成静态契约：
  ① 正文 payload 里不许再拼话题（话题要单独敲）；
  ② 必须逐个 keyboard.type 敲 `#关键词`；
  ③ 必须回读 `span.topic / span[data-type=topic]` 拿到 landed；
  ④ 结果里必须带 topics_declared / topics_landed / topics_ok；
  ⑤ 话题不满要重试一次，且不阻断发布（只告警 + 如实记录）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SRC = (PROJ / "src" / "providers" / "channels_cdp.py").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def main() -> int:
    print("=== 视频号话题契约 ===")
    check("① payload 不拼话题（tag_str 不进描述正文）",
          'payload = f"{title}\\n\\n{_cap_lines(content_text)}"' in SRC)
    check("① 旧写法已移除（payload 里不再带 tag_str）",
          'payload = f"{title}\\n\\n{_cap_lines(content_text)}\\n\\n{tag_str}"' not in SRC)
    check("② 逐个 keyboard.type 敲 #关键词",
          'await page.keyboard.type(f"#{_tag} ", delay=110)' in SRC)
    check("② 敲之前把光标移到编辑器末尾",
          "r.selectNodeContents(ed)" in SRC and "r.collapse(false)" in SRC)
    check("③ 回读话题节点 span.topic / [data-type=topic]",
          "querySelectorAll('span.topic, span[data-type=" in SRC)
    check("④ 结果带 topics_declared/topics_landed/topics_ok",
          all(f'"{k}"' in SRC for k in ("topics_declared", "topics_landed", "topics_ok")))
    check("⑤ 不满会重试一次",
          "话题重试后" in SRC)
    check("⑤ 话题失败不阻断发布（只告警）",
          "话题敲入失败（不阻断发布）" in SRC and "话题未全部生效" in SRC)
    check("停止点（stop_before_publish）也返回话题证据",
          '"topics_declared": topics_declared,\n                            "topics_landed": topics_landed,' in SRC)
    # 话题归一化：draft 里的 # 前缀不影响比对
    check("话题归一化去 # 前缀", 'lstrip("#")' in SRC)

    print(f"\nCHANNELS TOPICS CONTRACT {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
