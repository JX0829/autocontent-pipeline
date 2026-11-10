#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.content_fingerprint（gate, warn）—— 内容指纹查重（T2.4）

`fp = sha256(normalize_text(title + slug + body))[:16]`，归一化走 `src.utils.normalize`
（与评论判重同一套实现，QA #11 的解法）。

判定：查 `pipeline_runs.content_hash` 是否已有同指纹的 **其它 task**
  · 有 → `ok=false`（on_fail: warn，记录后继续，但台账里留痕）
  · 无 → 登记本次指纹到 `data/content_fingerprints.jsonl` 并放行
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))
from src.utils.normalize import content_hash as full_hash, normalize_text  # noqa: E402

LEDGER = C.ledger_path("content_fingerprints")


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script")) or {}

    title = str((draft or {}).get("title") or man.get("title") or "")
    body = str((draft or {}).get("body") or man.get("body") or "")
    slug = str((draft or {}).get("slug") or c.get("slug") or "")
    if not title and not body:
        C.fail("content_fingerprint: title 与 body 都为空，无法生成指纹")

    full = full_hash(title, body, slug)
    fp = full[:16]
    task_id = str(p.get("task_id") or "")

    # 1) 查 pipeline_runs（content_hash 是整个 64 位 sha256）
    try:
        from src.db import PipelineDB
        db = PipelineDB(str(C.db_path(p)))
        existing = db.find_run_by_content(full, str(c.get("account") or "unknown"))
    except Exception:
        existing = None

    if existing and str(existing.get("task_id")) != task_id:
        C.fail(f"content_fingerprint: 内容指纹命中已有 run（{existing.get('run_id')} status={existing.get('status')}）",
               fp=fp, matched_run=existing.get("run_id"))

    # 2) 查本地台账（含尚无 DB 记录的历史）
    rows = C.read_jsonl(LEDGER, limit=2000)
    dup = [r for r in rows if r.get("fp") == fp and r.get("task_id") != task_id]
    if dup:
        C.fail(f"content_fingerprint: 内容指纹重复（历史任务 {dup[-1].get('task_id')}）", fp=fp)

    if not any(r.get("fp") == fp and r.get("task_id") == task_id for r in rows):
        C.append_jsonl(LEDGER, {
            "ts": C.now_iso(), "task_id": task_id, "fp": fp,
            "norm_len": len(normalize_text(title + slug + body)), "title": title[:80],
        })

    C.emit(True, patch={
        "content_fp": fp,
        "content_fp_full": full,
        "norm_len": len(normalize_text(title + slug + body)),
        "task_id": task_id,
    })


if __name__ == "__main__":
    main()
