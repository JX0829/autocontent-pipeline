#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB 迁移脚本（T2.2 四表 + 兼容列）—— **默认 dry-run，不写库**

设计动机：迁移是最容易"跑一下就把生产库改了"的操作。所以默认行为是
**只读比对**，把「将要执行的 DDL」全部列出来；真要改必须显式 `--apply`。

做法（dry-run）：
  1. 在临时目录建一个**影子库**，对它跑 `PipelineDB.init_db()` → 得到"目标 schema"
  2. 以 `mode=ro` 只读打开真实库
  3. 比对 表 / 列 / 索引 三级差异，输出将要执行的变更清单
  4. 全程不写真实库（不建文件、不建表、不 ALTER）

用法：
    python3 scripts/migrate_db.py --dry-run          # 默认就是 dry-run
    python3 scripts/migrate_db.py --json
    python3 scripts/migrate_db.py --apply            # 真正执行（唯一会写库的入口）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def target_schema() -> dict:
    """用影子库拿到"目标 schema"（表、列、索引）。不改真实库。"""
    from src.db import PipelineDB
    tmpdir = Path(tempfile.mkdtemp(prefix="migrate_shadow_"))
    shadow = tmpdir / "shadow.db"
    try:
        PipelineDB(str(shadow))
        conn = sqlite3.connect(str(shadow))
        try:
            return _read_schema(conn)
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _read_schema(conn: sqlite3.Connection) -> dict:
    """读出表结构：{table: {"columns": [...], "sql": "...", "indexes": {name: sql}}}"""
    schema: dict = {}
    rows = conn.execute(
        "select name, sql from sqlite_master where type='table' and name not like 'sqlite_%'"
    ).fetchall()
    for name, sql in rows:
        cols = [(r[1], r[2], r[4]) for r in conn.execute(f"PRAGMA table_info('{name}')")]
        idx = {r[0]: r[1] for r in conn.execute(
            "select name, sql from sqlite_master where type='index' and tbl_name=? and sql is not null",
            (name,))}
        schema[name] = {"columns": cols, "sql": (sql or "").strip(), "indexes": idx}
    return schema


def open_readonly(db: Path) -> sqlite3.Connection | None:
    if not db.exists():
        return None
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)


def plan(db: Path) -> dict:
    """生成迁移计划（纯只读）。"""
    want = target_schema()
    conn = open_readonly(db)
    if conn is None:
        return {
            "db": str(db), "exists": False,
            "actions": [{"op": "CREATE_DB", "detail": "数据库文件不存在，将新建"}] + [
                {"op": "CREATE_TABLE", "table": t, "detail": "全部表"} for t in sorted(want)
            ],
            "summary": {"create_db": True, "create_table": len(want), "add_column": 0,
                        "create_index": sum(len(v["indexes"]) for v in want.values())},
        }
    try:
        have = _read_schema(conn)
        # 显式列一下索引（_read_schema 已按表归类）；额外抓独立索引
        extra_idx = {r[0]: r[1] for r in conn.execute(
            "select name, sql from sqlite_master where type='index' and sql is not null")}
    finally:
        conn.close()

    actions: list[dict] = []
    for t, spec in want.items():
        if t not in have:
            actions.append({"op": "CREATE_TABLE", "table": t,
                            "detail": spec["sql"].replace("\n", " ")[:140]})
            for name, isql in spec["indexes"].items():
                actions.append({"op": "CREATE_INDEX", "table": t, "name": name,
                                "detail": (isql or "").replace("\n", " ")[:120]})
            continue

        have_cols = {c[0] for c in have[t]["columns"]}
        for col, ctype, _notnull in spec["columns"]:
            if col not in have_cols:
                actions.append({"op": "ADD_COLUMN", "table": t, "column": col,
                                "detail": f"ALTER TABLE {t} ADD COLUMN {col} {ctype}"})

        for name, isql in spec["indexes"].items():
            if name not in extra_idx:
                actions.append({"op": "CREATE_INDEX", "table": t, "name": name,
                                "detail": (isql or "").replace("\n", " ")[:120]})

    summary = {"create_db": False,
               "create_table": sum(1 for a in actions if a["op"] == "CREATE_TABLE"),
               "add_column": sum(1 for a in actions if a["op"] == "ADD_COLUMN"),
               "create_index": sum(1 for a in actions if a["op"] == "CREATE_INDEX")}
    return {"db": str(db), "exists": True, "tables_have": sorted(have),
            "tables_want": sorted(want), "actions": actions, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DB 迁移（默认 dry-run）")
    ap.add_argument("--db", default=None, help="目标库（默认取 SQLITE_DB_PATH 或 data/pipeline_state.db）")
    ap.add_argument("--dry-run", action="store_true", default=True, help="只比对不写库（默认）")
    ap.add_argument("--apply", action="store_true", help="真正执行迁移（会写库）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db or os.getenv("SQLITE_DB_PATH") or PROJ / "data" / "pipeline_state.db")
    before = None
    if db.exists():
        st = db.stat()
        before = {"size": st.st_size, "mtime": int(st.st_mtime)}

    result = plan(db)
    result["mode"] = "APPLY" if args.apply else "DRY-RUN"
    result["db_before"] = before

    if args.apply:
        from src.db import PipelineDB
        PipelineDB(str(db))           # ← 唯一会写库的调用
        after = plan(db)
        result["remaining_actions"] = after["actions"]
        result["applied"] = not after["actions"]

    if db.exists():
        st = db.stat()
        result["db_after"] = {"size": st.st_size, "mtime": int(st.st_mtime)}
        result["db_unchanged"] = (before == result["db_after"])

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        s = result["summary"]
        print("=== 迁移" + ("执行" if args.apply else " dry-run（不写库）") + " ===")
        print(f"  目标库 : {db}")
        print(f"  是否存在: {'是' if result['exists'] else '否'}")
        if result["exists"]:
            missing = set(result["tables_want"]) - set(result["tables_have"])
            print(f"  现有表 : {', '.join(result['tables_have'])}")
            print(f"  缺失表 : {', '.join(sorted(missing)) if missing else '（无）'}")
        print(f"  计划变更: 建表 {s['create_table']} · 加列 {s['add_column']} · 建索引 {s['create_index']}")
        if result["actions"]:
            for a in result["actions"]:
                if a["op"] == "ADD_COLUMN":
                    print(f"    · ADD_COLUMN  {a['table']}.{a['column']}")
                elif a["op"] == "CREATE_TABLE":
                    print(f"    · CREATE_TABLE {a['table']}")
                elif a["op"] == "CREATE_INDEX":
                    print(f"    · CREATE_INDEX {a['name']} ON {a['table']}")
                else:
                    print(f"    · {a['op']} {a.get('detail', '')}")
        else:
            print("    （无需变更，schema 已是最新）")
        if not args.apply:
            print("  写库   : 否（dry-run 全程只读）")
        elif result.get("applied"):
            print("  写库   : 是 → 迁移完成，schema 已对齐")
    return 0


if __name__ == "__main__":
    sys.exit(main())
