#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.silence_guard（gate, warn）—— 静默失败护栏（T2.5）

QA #1/#3/#8 的共同母题：流程"成功"结束，但实际降级/空产出/吞了异常。

判据（任一成立即 `ok=false`，`on_fail: warn` 记录后继续，但必须告警）：
  1. 本 task 在 `degraded_events` 里有记录 → `reason="degraded:N"`
  2. 本步骤声明的产物一个都不在磁盘 / 存在但 0 字节
  3. draft/narration 为空或近乎为空
  4. `context.degraded_count > 0`（上游自报降级）

告警落地：`data/alerts.jsonl`（append-only），便于事后统计"这条片子降级过几次"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))
ALERTS = C.ledger_path("alerts")


def _degraded_count(task_id: str, db_file: Path, step_id: str | None) -> int:
    """查该 task（可限 step）的降级条数。库/表缺失返回 0（不伪造降级）。"""
    if not db_file.exists():
        return 0
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
        try:
            has = conn.execute(
                "select name from sqlite_master where type='table' and name='degraded_events'"
            ).fetchone()
            if not has:
                return 0
            if step_id:
                row = conn.execute(
                    "select count(*) from degraded_events where task_id=? and step_id=?",
                    (task_id, step_id)).fetchone()
            else:
                row = conn.execute(
                    "select count(*) from degraded_events where task_id=?", (task_id,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    task_id = str(p.get("task_id") or "")
    step_id = str(p.get("step_id") or "")
    problems: list[str] = []

    # 1. 降级台账
    n_degraded = _degraded_count(task_id, C.db_path(p), step_id or None)
    if n_degraded:
        problems.append(f"degraded:{n_degraded}")

    # 2. 声明产物 vs 磁盘真值
    declared = p.get("artifacts") or {}
    present = []
    for key in declared:
        path = C.artifact(p, str(key))
        if path is not None and path.exists() and path.stat().st_size > 0:
            present.append(str(key))
    if declared and not present:
        problems.append(f"产物全缺（声明 {len(declared)} 个，磁盘 0 个）")

    # 3. 空产出
    draft = c.get("draft") or {}
    body = str((draft or {}).get("body") or "")
    if draft and len(body.strip()) < 20:
        problems.append(f"draft.body 仅 {len(body.strip())} 字")
    narration = c.get("narration")
    if isinstance(narration, list) and narration and sum(len(str(x)) for x in narration) < 10:
        problems.append("narration 近乎为空")

    # 4. 上游自报降级
    upstream = int(c.get("degraded_count") or 0)
    if upstream > 0:
        problems.append(f"上游 degraded_count={upstream}")

    if not problems:
        C.emit(True, patch={"silence_guard": "ok", "artifacts_present": len(present),
                            "degraded_events": 0})

    reason = "silence_guard: " + "; ".join(problems)
    try:
        C.append_jsonl(ALERTS, {"ts": C.now_iso(), "task_id": task_id, "step_id": step_id,
                                "degraded_events": n_degraded, "problems": problems,
                                "artifacts_present": present})
    except OSError:
        pass
    C.fail(reason, degraded=n_degraded, artifacts_present=present)


if __name__ == "__main__":
    main()
