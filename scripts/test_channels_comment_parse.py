#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号评论解析契约（方案 A 止血，2026-11-22）。

事故复现：旧实现用 `.comment-item, tr, [class*='comment']` 兜底扫 DOM，
把「作品 feed 卡片」当评论 → 飞书卡片出现「粉丝=评论管理 / 评论=视频 / 作品=0」，
并往 seen 缓存攒了 72 条垃圾。

判据：
1. 结构化节点必须同时有「用户名 + 评论正文」；缺一个就不算评论
2. 页头 tab 文案、时间戳、纯数字（未读数）、feed 卡片特征 → 一律不认
3. 真评论照常返回
4. 作品级提醒记录里**不含** user / comment / reply 字段（结构上杜绝错位）
5. 同一作品评论数没变 → 不重复报

跑法：python3 scripts/test_channels_comment_parse.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.providers.channels_comments import (  # noqa: E402
    pick_comment, work_digest_records, works_from_post_list,
)

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def main() -> int:
    print("── ① 拒绝「版式当评论」（真机错位现场）──")
    # 真机卡片：作品正文首段 / 发布时间 / 未读数，被旧实现切成了 粉丝/评论/作品
    feed = {"user": "特斯拉的FSD又要跨一个大版本了", "content": "2026/11/22 11:12",
            "raw": "特斯拉的FSD又要跨一个大版本了\n2026/11/22 11:12\n2\n未读"}
    check("feed 卡片（用户名=正文首句）被拒", pick_comment(feed) is None, str(pick_comment(feed)))
    check("页头 tab（评论管理/视频）被拒",
          pick_comment({"user": "评论管理", "content": "视频", "raw": "评论管理\n视频"}) is None)
    check("内容为时间戳被拒",
          pick_comment({"user": "某粉丝", "content": "2026/11/22 08:36", "raw": "x"}) is None)
    check("内容为纯数字（未读数）被拒",
          pick_comment({"user": "某粉丝", "content": "2", "raw": "x"}) is None)
    check("只有用户名没有正文 → 被拒",
          pick_comment({"user": "某粉丝", "content": "", "raw": "某粉丝"}) is None)
    check("feed 特征词满屏（含两个以上标记）→ 被拒",
          pick_comment({"user": "某粉丝", "content": "未读数 图文 置顶", "raw": "未读 图文 置顶"}) is None)

    print("── ② 真评论照常识别 ──")
    real = pick_comment({"user": "江湖骗子_9527", "content": "这个推理延迟测过吗？", "raw": "江湖骗子_9527\n这个推理延迟测过吗？"})
    check("真评论被接受", real is not None and real["user"] == "江湖骗子_9527", str(real))
    check("正文完整保留", real is not None and "延迟" in real["content"], str(real and real["content"]))

    print("── ③ post_list → 作品级摘要 ──")
    data = {"data": {"list": [
        {"objectId": "export/A", "commentCount": 5,
         "desc": {"description": "英伟达动作模型跑到15000帧\n\n正文……"}},
        {"objectId": "export/B", "commentCount": 0, "desc": {"description": "无评论的作品"}},
        {"objectId": "export/C", "commentCount": 2, "desc": {"description": "微软把2点3B模型塞进Windows"}},
    ]}}
    works = works_from_post_list(data)
    check("解析出 3 条作品", len(works) == 3, str(len(works)))
    check("标题取首行", works[0]["title"] == "英伟达动作模型跑到15000帧", works[0]["title"])

    digests = work_digest_records(works, {})
    check("只报有评论的作品（2 条）", len(digests) == 2, str([d["video_title"] for d in digests]))
    d0 = digests[0]
    check("记录里没有 user 字段", "user" not in d0, str(list(d0)))
    check("记录里没有 comment 字段", "comment" not in d0, str(list(d0)))
    check("记录里没有 reply 字段", "reply" not in d0, str(list(d0)))
    check("kind=work_unread", d0.get("kind") == "work_unread", str(d0.get("kind")))
    check("评论数带过来", d0.get("comment_count") == 5, str(d0.get("comment_count")))

    print("── ④ 数量没变不重复报 ──")
    again = work_digest_records(works, {"export/A": 5, "export/C": 2})
    check("评论数未变化 → 不再报", again == [], str(again))
    more = work_digest_records(works, {"export/A": 4})
    check("评论数增加 → 重新报（含 +N）", len(more) == 2
          and any(d["object_id"] == "export/A" and d["comment_count"] == 5 for d in more), str(more))

    print(f"\nCHANNELS COMMENT PARSE {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
