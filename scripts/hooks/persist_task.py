#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.persist_task（transform）—— 投料即落台账。

把 manifest 写入 `content_tasks`（按 entity_key 幂等 upsert），并向后续步骤回传
规范化的 task_id / entity_key。表结构由 T2.2 的迁移统一管理，此处只写入不建表：
表缺失 → ok=false（宁可明确失败，也不要"看起来落库了其实没落"）。
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))   # T3.1：要 import src.utils.normalize


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or C.read_json(C.artifact(p, "manifest")) or {}
    title = str(man.get("title") or "").strip()
    if not title:
        C.fail("persist_task: manifest.title 为空，无法落台账")

    task_id = str(p.get("task_id") or man.get("task_id") or "")
    if not task_id:
        C.fail("persist_task: 缺 task_id")
    # T3.1：entity_key 统一走 normalize_entity_key（剥尾部 unix 时间戳 + 小写），
    # 保证同一事件重跑命中的是同一个 key，UNIQUE(entity_key) 才能生效
    from src.utils.normalize import normalize_entity_key
    entity_key = normalize_entity_key(str(man.get("entity_key") or "")) or C.normalize_text(title)[:120]
    db = C.db_path(p)
    if not db.exists():
        C.fail(f"persist_task: 数据库不存在 {db}（先跑 T2.2 迁移）")

    row = (
        task_id,
        str(man.get("source_url") or ""),
        entity_key,
        title,
        str(c.get("account") or man.get("account") or "unknown"),
        "intake",
        str(man.get("body") or ""),
    )
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        try:
            conn.execute(
                """insert into content_tasks
                       (task_id, source_url, entity_key, theme, target_account_id, status, fact_summary_150)
                   values (?,?,?,?,?,?,?)
                   on conflict(entity_key) do update set
                       theme=excluded.theme,
                       target_account_id=excluded.target_account_id,
                       updated_at=CURRENT_TIMESTAMP""",
                row,
            )
            conn.commit()
            existing_task = conn.execute(
                "select task_id from content_tasks where entity_key=?", (entity_key,)
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        C.fail(f"persist_task: 写入失败（表结构缺失？T2.2）: {exc}")

    C.emit(True, patch={
        "task_id": existing_task[0] if existing_task else task_id,
        "entity_key": entity_key,
        "persisted": True,
        "db": str(db),
    })


if __name__ == "__main__":
    main()
