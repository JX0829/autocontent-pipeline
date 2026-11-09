#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""entity_key 归一化迁移（T3.1 · 高风险：动历史数据）

病根（QA R1）：`entity_key` 形如 `hackernews:claude-code-...-1794114373`，尾部是
unix 时间戳 → 同一事件重跑生成新 key，`UNIQUE(entity_key)` 永不触发，重复选题直接入库。

本脚本把尾部时间戳剥掉。**默认 dry-run，不写库**：

    python3 scripts/migrate_entity_key.py                # dry-run：报告将变更 N 行 + 冲突分析
    python3 scripts/migrate_entity_key.py --apply        # 有冲突时**拒绝执行**（需人工裁决）
    python3 scripts/migrate_entity_key.py --apply --resolve-conflicts   # 按建议方案落库

冲突处理原则（不静默）：
  归一化后两行撞成同一个 key 时，**保留"进展更靠后"的那条**（DISPATCHED/PUBLISHED
  优先于 SCRIPTED），落败行改成 `<key>~dup<序号>` 并写入 `migration_report` 供人工复核。
  不给 `--resolve-conflicts` 就一行都不改 —— 冲突必须由人拍板。

写库前自动备份到 `.backup/<日期>/pipeline_state_before_entity_key_<ts>.db`。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
from src.utils.normalize import normalize_entity_key  # noqa: E402

STATUS_RANK = {  # 越靠后越"该保留"
    "INTAKE": 0, "SCRIPTED": 1, "RENDERED": 2, "APPROVED": 3,
    "DISPATCHED": 4, "PUBLISHED": 5,
}


def load_rows(db: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "select task_id, entity_key, status, created_at from content_tasks order by created_at")]
    finally:
        conn.close()


def build_plan(rows: list[dict]) -> dict:
    changes: list[dict] = []
    for r in rows:
        old = str(r["entity_key"] or "")
        new = normalize_entity_key(old)
        if new and new != old:
            changes.append({"task_id": r["task_id"], "old": old, "new": new,
                            "status": r["status"], "created_at": r["created_at"]})

    # 冲突：归一化后与"未变更行"或"其它变更行"撞 key
    taken: dict[str, list[str]] = {}
    for r in rows:
        key = normalize_entity_key(str(r["entity_key"] or ""))
        if key:
            taken.setdefault(key, []).append(r["task_id"])

    conflicts = []
    for key, task_ids in taken.items():
        if len(task_ids) > 1:
            # 按状态优先级 + 创建时间决定保留谁
            ranked = sorted(task_ids, key=lambda tid: (
                STATUS_RANK.get(next((r["status"] for r in rows if r["task_id"] == tid), ""), -1),
                next((str(r["created_at"] or "") for r in rows if r["task_id"] == tid), ""),
            ), reverse=True)
            winner, losers = ranked[0], ranked[1:]
            conflicts.append({"key": key, "winner": winner, "losers": losers,
                              "resolution": {loser: f"{key}~dup{i + 2}" for i, loser in enumerate(losers)}})

    return {"total_rows": len(rows), "to_change": len(changes),
            "changes": changes, "conflicts": conflicts}


def backup(db: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = PROJ / ".backup" / datetime.now().strftime("%Y%m%d")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"pipeline_state_before_entity_key_{ts}.db"
    shutil.copyfile(db, dest)
    return dest


def apply(db: Path, plan: dict, resolve: bool) -> tuple[bool, str]:
    if plan["conflicts"] and not resolve:
        return False, "存在归一化冲突，已拒绝写库（加 --resolve-conflicts 才按建议方案执行）"

    dest = backup(db)
    conn = sqlite3.connect(str(db), timeout=10)
    try:
        with conn:  # 单事务：任何 IntegrityError 整体回滚
            for ch in plan["changes"]:
                new_key = ch["new"]
                for c in plan["conflicts"]:
                    if ch["task_id"] in c["resolution"]:
                        new_key = c["resolution"][ch["task_id"]]
                conn.execute("update content_tasks set entity_key=?, updated_at=CURRENT_TIMESTAMP "
                             "where task_id=?", (new_key, ch["task_id"]))
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        conn.close()
        return False, f"UNIQUE 冲突，已回滚：{exc}（备份：{dest}）"
    conn.close()

    # 迁移报告（人工复核用）
    report = PROJ / ".backup" / "entity_key_migration_report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                  "backup": str(dest), "plan": plan}, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return True, f"已写库 {len(plan['changes'])} 行（备份：{dest.name}，报告：{report.name}）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="entity_key 归一化迁移（默认 dry-run）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只读）")
    ap.add_argument("--resolve-conflicts", action="store_true",
                    help="按建议方案处理同名冲突（落败行改 ~dupN）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db or PROJ / "data" / "pipeline_state.db")
    if not db.exists():
        print(f"库不存在: {db}")
        return 2

    plan = build_plan(load_rows(db))

    if args.json and not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    if not args.apply:
        print("=== entity_key 归一化 dry-run（不写库）===")
        print(f"  库      : {db}")
        print(f"  总行数  : {plan['total_rows']}")
        print(f"  将变更  : {plan['to_change']} 行")
        for ch in plan["changes"]:
            print(f"    · {ch['task_id']}")
            print(f"        {ch['old']}")
            print(f"     →  {ch['new']}")
        if plan["conflicts"]:
            print(f"  冲突    : {len(plan['conflicts'])} 组（apply 默认拒绝，需人工裁决）")
            for c in plan["conflicts"]:
                print(f"    ! {c['key']}")
                print(f"        保留: {c['winner']}")
                for loser, newk in c["resolution"].items():
                    print(f"        落败: {loser} → {newk}")
        else:
            print("  冲突    : 无")
        print("  写库    : 否（dry-run）")
        return 0

    ok, msg = apply(db, plan, args.resolve_conflicts)
    print(("=== apply " + ("成功" if ok else "中止") + " ==="))
    print(f"  {msg}")
    if ok:
        rows = load_rows(db)
        keys = [r["entity_key"] for r in rows]
        print(f"  校验: 总行 {len(rows)} · 唯一 key {len(set(keys))}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
