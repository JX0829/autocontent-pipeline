#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.send_ledger（gate, block）—— 发送台账幂等闸门（T2.3）

`dedupe_key = sha256(content_hash | video_sha256 | account)`，靠
`send_ledger.dedupe_key` 主键 + `INSERT ... ON CONFLICT DO NOTHING` 抢占发送位：

  · 抢到（rowcount==1）  → `ok=true`，表示「本次是首次发送」
  · 抢不到               → `ok=false, reason="already_sent"`，**绝不重复发**

为什么不用「先查再插」：并发下两条流水线会同时查到"没发过"然后一起发。
为什么 key 里要有 video_sha256：同内容重渲染出新片子（换了封面/字幕）算新产物，
允许发；而同一份视频重复投递必须被拦。
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))


def _content_hash(p: dict, c: dict, man: dict, draft: dict) -> str:
    """优先用上游已算好的 content_hash，避免重复实现归一化口径。"""
    given = str(c.get("content_hash") or "").strip()
    if given:
        return given
    from src.utils.normalize import content_hash as ch
    title = str(draft.get("title") or man.get("title") or "")
    body = str(draft.get("body") or man.get("body") or "")
    slug = str(draft.get("slug") or c.get("slug") or "")
    return ch(title, body, slug)


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or {}

    # 视频产物：优先 video_16_9 / vertical，取第一个存在的
    video = None
    for key in (c.get("video_key"), "video_16_9", "video", "vertical"):
        if not key:
            continue
        cand = C.artifact(p, str(key))
        if cand is not None and cand.exists():
            video = cand
            break
    if video is None:
        C.fail("send_ledger: 找不到待发视频产物（artifacts.video_16_9 / video）")

    account = str(c.get("account") or "")
    if not account:
        C.fail("send_ledger: 缺 context.account，无法判定投递对象")

    content_hash = _content_hash(p, c, man, draft)
    video_sha = C.sha256_file(video)
    channel = str(c.get("channel") or c.get("platform") or "feishu")
    chat_id = str(c.get("chat_id") or c.get("account") or "")
    dedupe_key = hashlib.sha256(
        f"{content_hash}|{video_sha}|{account}".encode("utf-8")
    ).hexdigest()

    try:
        from src.db import PipelineDB
        db = PipelineDB(str(C.db_path(p)))
    except Exception as exc:
        # 台账不可用 → 失败关闭：宁可发不出去，也不能重复发
        C.fail(f"send_ledger: 台账不可用，拒绝发送（fail closed）: {exc}")

    task_id = str(p.get("task_id") or "")
    first = db.claim_send_slot(dedupe_key, task_id, channel, chat_id, str(video))
    if not first:
        C.fail("already_sent", dedupe_key=dedupe_key[:16],
               channel=channel, chat_id=chat_id, video_sha256=video_sha[:16])

    C.emit(True, patch={
        "dedupe_key": dedupe_key[:16],
        "dedupe_key_full": dedupe_key,
        "channel": channel,
        "chat_id": chat_id,
        "video_sha256": video_sha[:16],
        "content_hash": content_hash[:16],
        "claimed": True,
    })


if __name__ == "__main__":
    main()
