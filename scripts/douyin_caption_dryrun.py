#!/usr/bin/env python3.14
"""Dry Run：抖音「作品描述」排版/字符校验器（离线，不碰平台）。

验证两件事：
1. 现有平台上的真实描述（从编辑页读回的原文）能被校验器判为「不合格」——
   说明检测有效：换行=0、零宽空格 5 个、NBSP 8 个。
2. 修好后的写法（标题 / 简介 / 话题分段）能被判为「合格」。

用法: python douyin_caption_dryrun.py
"""
import re
import sys
from pathlib import Path

import json

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

# 平台编辑页真实读回的文本（2026-11-15 01:xx，作品「黄仁勋辞任 英伟达突然换帅」）
PLATFORM_ACTUAL = (
    "黄仁勋辞任 英伟达突然换帅 \u200b\xa0#英伟达\xa0 #黄仁勋 \u200b\xa0#AI算力\xa0 "
    "\u200b\xa0#科技巨头\xa0 \u200b\xa0#行业人事\xa0 英伟达官方公告确认，黄仁勋辞去总裁、"
    "首席执行官和董事职务，即时生效，33年的任期到这里结束。他把这家做显卡的公司带成了"
    "4万亿美元市值的AI算力核心。接棒的是现任首席财务官科莱特·克雷斯，她以临时首席执行官"
    "的身份继续兼任原来的财务职位。董事会已经启动永久首席执行官的遴选，内部和外部人选"
    "都在考虑范围内。同时公司第一次设立了董事会主席，由Harvey C. Jones担任。这次人事变动"
    "发生得相当突然，后续架构怎么走值得持续关注。\u200b"
)

from src.utils.douyin_caption import build_caption, verify_caption  # 唯一实现


def main() -> int:
    sample = PROJ / "output/20261112/nvda-resign-6/draft_script.json"
    if sample.exists():
        draft = json.loads(sample.read_text(encoding="utf-8"))
    else:
        # worktree / 新机器没有历史 output：用同一份样本兜底，保证离线可跑
        draft = {"title": "黄仁勋辞任 英伟达突然换帅",
                 "video_desc": "英伟达官方公告确认，黄仁勋辞去总裁、首席执行官和董事职务，即时生效。",
                 "topics": ["英伟达", "黄仁勋", "AI算力", "科技巨头", "行业人事"]}
    title = str(draft.get("title") or "")
    desc = str(draft.get("video_desc") or "")
    topics = list(draft.get("topics") or draft.get("tags") or [])

    old_expected = f"{title} " + " ".join(f"#{t}" for t in topics) + f"\n{desc}"
    new_expected = build_caption(title, desc, topics)

    print("=== ① 现状：平台读回 vs 旧期望 ===")
    r1 = verify_caption(PLATFORM_ACTUAL, old_expected)
    print(f"  ok={r1['ok']} 换行={r1['newlines']} 零宽={r1['zw']} NBSP={r1['nbsp']}")
    for p in r1["problems"]:
        print("   ✗", p)

    print("\n=== ② 新写法：三段式文本 ===")
    print("  ", repr(new_expected[:110]) + " …")
    r2 = verify_caption(new_expected, new_expected)
    print(f"  ok={r2['ok']} 换行={r2['newlines']} 零宽={r2['zw']} NBSP={r2['nbsp']}")

    print("\n=== ③ 反向用例：如果 insert_text 仍然吞换行，校验器要能拦住 ===")
    flat = new_expected.replace("\n\n", " ")
    r3 = verify_caption(flat, new_expected)
    print(f"  ok={r3['ok']}（期望 False）")
    for p in r3["problems"]:
        print("   ✗", p)

    print("\n=== ④ 反向用例：夹带零宽字符要能拦住 ===")
    dirty = new_expected.replace("#英伟达", "\u200b\xa0#英伟达\xa0")
    r4 = verify_caption(dirty, new_expected)
    print(f"  ok={r4['ok']}（期望 False） 零宽={r4['zw']} NBSP={r4['nbsp']}")

    good = r1["ok"] is False and r2["ok"] is True and r3["ok"] is False and r4["ok"] is False
    print(f"\nDRY RUN {'OK' if good else 'FAIL'}（检测器能拦住平台当前的乱排版）")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
