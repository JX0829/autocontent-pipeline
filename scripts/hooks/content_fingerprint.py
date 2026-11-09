#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.content_fingerprint（gate, warn）—— 内容指纹查重。

指纹 = sha256(归一化 title + 归一化 body 前 2000 字)。
命中 `data/content_fingerprints.jsonl` 里已有指纹 → warn（可能是改写重复选题）。
每次运行都会把本次指纹追加进台账（幂等：同指纹同日只记一次）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

LEDGER = C.ledger_path("content_fingerprints")


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script"))

    title = str((draft or {}).get("title") or man.get("title") or "")
    body = str((draft or {}).get("body") or man.get("body") or "")
    if not title and not body:
        C.fail("content_fingerprint: title 与 body 都为空，无法生成指纹")

    fp = C.sha256_text(C.normalize_text(title) + "|" + C.normalize_text(body)[:2000])
    task_id = str(p.get("task_id") or "")
    rows = C.read_jsonl(LEDGER, limit=2000)
    same = [r for r in rows if r.get("fp") == fp and r.get("task_id") != task_id]

    if same:
        C.fail(f"content_fingerprint: 内容指纹重复（历史任务 {same[-1].get('task_id')}）", fp=fp[:16])

    if not any(r.get("fp") == fp and r.get("task_id") == task_id for r in rows):
        C.append_jsonl(LEDGER, {"ts": C.now_iso(), "task_id": task_id, "fp": fp, "title": title[:80]})

    C.emit(True, patch={"content_fp": fp[:16], "content_fp_full": fp})


if __name__ == "__main__":
    main()
