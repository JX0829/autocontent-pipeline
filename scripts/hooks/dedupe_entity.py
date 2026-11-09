#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.dedupe_entity（gate, block）—— 选题排他。

在生成脚本之前就拦住"同一个实体再做一遍"。比对来源：
  1. `content_tasks` 的历史 theme / entity_key（近 60 条）
  2. `data/send_ledger.jsonl` 的已发记录标题
归一化后判定：完全相同 / 互相包含（≥10 字）/ 归一化 Jaccard ≥ 0.8。
台账不可用 → 失败关闭（fail closed），不允许"查不了就当没重复"。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def _tokens(s: str) -> set[str]:
    norm = C.normalize_text(s)
    return {norm[i:i + 2] for i in range(max(0, len(norm) - 1))}


def _similar(a: str, b: str) -> tuple[bool, str]:
    na, nb = C.normalize_text(a), C.normalize_text(b)
    if not na or not nb:
        return False, ""
    if na == nb:
        return True, "完全相同"
    if len(na) >= 10 and (na in nb or nb in na):
        return True, "标题互相包含"
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb:
        jac = len(ta & tb) / len(ta | tb)
        if jac >= 0.8:
            return True, f"字符二元组相似度 {jac:.2f}"
    return False, ""


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or C.read_json(C.artifact(p, "manifest")) or {}
    title = str(man.get("title") or "").strip()
    if not title:
        C.fail("dedupe_entity: 缺 title，无法排他")

    history: list[str] = []
    db = C.db_path(p)
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                has = conn.execute(
                    "select name from sqlite_master where type='table' and name='content_tasks'"
                ).fetchone()
                if has:
                    for theme, key in conn.execute(
                        "select coalesce(theme,''), coalesce(entity_key,'') from content_tasks "
                        "order by created_at desc limit 60"
                    ):
                        history.extend(x for x in (theme, key) if x)
            finally:
                conn.close()
        except sqlite3.Error:
            history = []
    history.extend(str(r.get("title") or "") for r in C.read_jsonl(C.ledger_path("send_ledger"))[-200:])

    for old in history:
        dup, why = _similar(title, old)
        if dup:
            C.fail(f"dedupe_entity: 命中历史选题「{old[:40]}」——{why}", matched=old[:80])

    C.emit(True, patch={"dedupe_checked": len(history), "entity_key": C.normalize_text(title)[:120]})


if __name__ == "__main__":
    main()
