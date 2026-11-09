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

    # 待发产物：按 aspect 逐个预检（竖版 video.mp4 / 横版 video_16_9.mp4）
    # 只有**全部** aspect 都已发过才阻断；部分已发时必须放行，
    # 否则横版永远补不上（旧实现只取第一个存在的文件，横版被彻底跳过）。
    aspect_files = [("vertical", "video", "video.mp4"),
                    ("landscape", "video_16_9", "video_16_9.mp4")]
    targets = []
    for aspect, key, fname in aspect_files:
        cand = C.artifact(p, str(c.get(f"{aspect}_key") or key))
        if cand is None or not cand.exists():
            cand = C.artifact(p, fname)
        if cand is not None and cand.exists():
            targets.append((aspect, cand))
    if not targets:
        C.fail("send_ledger: 找不到待发视频产物（video.mp4 / video_16_9.mp4）")

    account = str(c.get("account") or "")
    if not account:
        C.fail("send_ledger: 缺 context.account，无法判定投递对象")

    content_hash = _content_hash(p, c, man, draft)
    channel = str(c.get("channel") or c.get("platform") or "feishu")
    chat_id = str(c.get("chat_id") or c.get("account") or "")

    keys = {}
    for aspect, path in targets:
        keys[aspect] = hashlib.sha256(
            f"{content_hash}|{C.sha256_file(path)}|{account}".encode("utf-8")
        ).hexdigest()
    # 兼容旧字段：主 key 取竖版（没有竖版则取第一个）
    dedupe_key = keys.get("vertical") or next(iter(keys.values()))

    try:
        from src.db import PipelineDB
        db = PipelineDB(str(C.db_path(p)))
    except Exception as exc:
        # 台账不可用 → 失败关闭：宁可发不出去，也不能重复发
        C.fail(f"send_ledger: 台账不可用，拒绝发送（fail closed）: {exc}")

    task_id = str(p.get("task_id") or "")
    # 只读预检，**不抢占**：抢占必须由执行体（exec_dispatch）独占完成，
    # 否则 hook 先 INSERT、执行体再 claim 必然返回 False → 被判 already_sent
    # → 台账有行但一条没发出去（GATE-3 实测踩到过）。
    try:
        conn = db.get_conn()
        sent = {}
        for aspect, k in keys.items():
            row = conn.execute(
                "select feishu_msg_id from send_ledger where dedupe_key=?", (k,)
            ).fetchone()
            if row is not None:
                sent[aspect] = row[0]
    except Exception as exc:  # noqa: BLE001
        C.fail(f"send_ledger: 台账不可读，拒绝发送（fail closed）: {exc}")

    pending = [a for a in keys if a not in sent]
    if not pending:
        C.fail("already_sent", dedupe_key=dedupe_key[:16], channel=channel,
               chat_id=chat_id, aspects=list(sent), feishu_msg_id=next(iter(sent.values())))

    C.emit(True, patch={
        "dedupe_key": dedupe_key[:16],
        "dedupe_key_full": dedupe_key,
        "dedupe_keys": {a: k[:16] for a, k in keys.items()},
        "channel": channel,
        "chat_id": chat_id,
        "content_hash": content_hash[:16],
        "already_sent_aspects": sent,
        "pending_aspects": pending,
        "claimed": False,   # 预检通过，实际抢占在执行体
    })


if __name__ == "__main__":
    main()
