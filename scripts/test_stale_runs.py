#!/usr/bin/env python3
"""僵尸 RUNNING 收尸逻辑回归

锁死三件事：
  1. 超过阈值没更新的 RUNNING → 判为僵尸
  2. 刚启动的 RUNNING（短于阈值）→ **不能**误杀
  3. 收尸后：run 状态变 ABANDONED，步骤层 RUNNING 同步改，产物与 run_root 不动
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.db import PipelineDB  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="stale_"))
    dbp = tmp / "state.db"
    db = PipelineDB(str(dbp))

    print("=== 僵尸 RUNNING 收尸回归 ===")

    # 造两条 run：一条 1 小时没动（僵尸），一条刚写（活的）
    db.upsert_run("zombie-1", "t1", "h1", "acct-test-a", str(tmp / "zombie"))
    db.set_step_status("zombie-1", "expand_script", "RUNNING")
    db.upsert_run("alive-1", "t2", "h2", "acct-test-a", str(tmp / "alive"))
    db.set_step_status("alive-1", "expand_script", "RUNNING")
    with sqlite3.connect(dbp) as c:
        c.execute("UPDATE pipeline_runs SET updated_at=datetime('now','-60 minutes') "
                  "WHERE run_id='zombie-1'")
        c.commit()

    print("\n[1] 判定")
    stale = db.list_stale_runs(15)
    ids = [r["run_id"] for r in stale]
    check("僵尸 run 被识别", "zombie-1" in ids, str(ids))
    check("刚启动的 run 不被误杀", "alive-1" not in ids, str(ids))

    print("\n[2] 收尸")
    done = db.fail_stale_runs(15)
    check("返回被清理的 run", done == ["zombie-1"], str(done))
    z = db.get_run("zombie-1") or {}
    check("run 状态变 ABANDONED", z.get("status") == "ABANDONED", str(z.get("status")))
    steps = json.loads(z.get("steps_json") or "{}")
    check("步骤层 RUNNING → ABANDONED", steps.get("expand_script") == "ABANDONED", str(steps))
    check("run_root 未被改动", z.get("run_root") == str(tmp / "zombie"), str(z.get("run_root")))
    a = db.get_run("alive-1") or {}
    check("活着的 run 状态不变", a.get("status") == "RUNNING", str(a.get("status")))

    print("\n[3] 幂等")
    check("再次收尸无事可做", db.fail_stale_runs(15) == [], "")

    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("STALE RUN CONTRACT FAIL")
        return 1
    print("STALE RUN CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
