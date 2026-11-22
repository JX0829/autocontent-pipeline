#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号评论：**只接受能证实的结构，拿不到就明说拿不到**（方案 A 止血）。

背景（2026-11-22 事故）：`poll_channels_comments.py` 用
`.comment-item, tr, [class*='comment']` 兜底扫 DOM，把「作品 feed 卡片」当成评论，
于是飞书卡片里出现「粉丝=评论管理 / 评论=视频 / 作品=0」这类错位内容，还往
`seen_channels_comments.json` 攒了 72 条垃圾 id，每 15 分钟发一次。

这里的判据是**宁缺勿假**：
1. 结构化评论节点必须同时给出「用户名」和「评论正文」，任缺一个就不是评论；
2. feed 卡片特征（未读、时间戳、图文/视频标签、页头 tab 文案）一律不认；
3. 一条都不成立 → 返回空，由调用方降级成「作品级未读提醒」，绝不编造粉丝/评论。
"""
from __future__ import annotations

import re
from typing import Any

#: 评论页页头 / feed 卡片的特征词：出现即判定「这不是评论」，避免把版式当内容
FEED_MARKERS = (
    "评论管理", "全部评论", "未读", "评论已关闭", "暂无评论",
    "图文", "发消息", "置顶", "作者赞过",
)

#: 用户名里出现这些词，说明抓到的是页头 tab / 操作按钮，不是真人昵称
USER_BLOCKLIST = ("评论管理", "全部评论", "未回复", "已回复", "筛选", "搜索", "排序")

#: 像「2026/11/22 11:12」「11:12」这种，是发布时间，不是评论内容
_DATETIME_RE = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}(\s+\d{1,2}:\d{2})?$|^\d{1,2}:\d{2}$")
#: 纯数字（未读数 / 播放量）
_PURE_NUM_RE = re.compile(r"^\d+$")


def looks_like_datetime(s: str) -> bool:
    return bool(_DATETIME_RE.match(str(s or "").strip()))


def pick_comment(cand: dict[str, Any]) -> dict[str, Any] | None:
    """从一条候选节点判定「这是不是一条真评论」。不是就返回 None。

    cand 形如 {"user": 昵称, "content": 正文, "raw": 节点全文}
    """
    user = str(cand.get("user") or "").strip()
    content = str(cand.get("content") or "").strip()
    raw = str(cand.get("raw") or "")

    if not user or not content:
        return None
    if user in USER_BLOCKLIST or any(b in user for b in USER_BLOCKLIST):
        return None
    if looks_like_datetime(content) or _PURE_NUM_RE.match(content):
        return None
    if len(content) < 2:
        return None
    # feed 卡片整块的特征词：真评论里不该同时出现两个以上
    hits = sum(1 for m in FEED_MARKERS if m in raw)
    if hits >= 2:
        return None
    return {"user": user[:40], "content": content[:150], "raw": raw[:400]}


def work_digest_records(works: list[dict[str, Any]], seen: dict[str, int],
                        limit: int = 8) -> list[dict[str, Any]]:
    """把 post_list 的作品变成「作品级未读提醒」记录（不含任何编造的粉丝/评论）。

    · 只报 `comment_count > 0` 的作品；
    · `seen` 里记过的 (object_id → 上次评论数) 且数量没变 → 不重复报；
    · 返回记录里**没有** user / comment / reply 字段，卡片层因此不可能再错位。
    """
    out: list[dict[str, Any]] = []
    for w in works[:limit]:
        oid = str(w.get("object_id") or "")
        cnt = int(w.get("comment_count") or 0)
        if not oid or cnt <= 0:
            continue
        if int(seen.get(oid, -1)) == cnt:
            continue
        out.append({
            "platform": "channels",
            "kind": "work_unread",
            "object_id": oid,
            "video_title": str(w.get("title") or "未知作品")[:60],
            "comment_count": cnt,
            "previous_count": int(seen.get(oid, 0)),
            "reply_mode": "work_unread_notice",
            "note": "视频号 Web 端当前拿不到评论正文，请到后台或手机端查看明细",
        })
    return out


def works_from_post_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """post_list 接口结果 → [{object_id, title, comment_count, create_time}]。"""
    items = ((data.get("data") or {}).get("list") or []) if isinstance(data, dict) else []
    works = []
    for it in items:
        desc = (it.get("desc") or {})
        text = str(desc.get("description") or "").strip()
        title = text.split("\n")[0] if text else str(desc.get("title") or "").strip()
        works.append({
            "object_id": str(it.get("objectId") or ""),
            "title": title,
            "comment_count": int(it.get("commentCount") or it.get("comment_count") or 0),
            "create_time": int(it.get("createTime") or 0),
        })
    return works
