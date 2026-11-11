#!/usr/bin/env python3
"""僵尸 RUNNING 收尸（默认 dry-run，只有 --apply 才写库）

背景：2026-11-10 的 `astryx-1` / `freecc-1` 卡在 expand_script，进程被外部杀掉后
没有任何代码路径写失败，状态库永远停在 RUNNING —— 后面同物料运行会误以为还在跑。

判据：`status=RUNNING` 且 `updated_at` 超过阈值（默认 15 分钟）没更新。
最长单步（渲渲染）约 2.5 分钟，阈值 15 分钟不会误杀活着的 run。

用法：
    python3 scripts/reap_stale_runs.py                 # 只看（dry-run）
    python3 scripts/reap_stale_runs.py --minutes 30    # 换阈值
    python3 scripts/reap_stale_runs.py --apply         # 真正标记为 ABANDONED
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.db import PipelineDB  # noqa: E402

DEFAULT_DB = os.getenv("PIPELINE_DB") or str(PROJ / "data" / "pipeline_state.db")


def main() -> int:
    ap = argparse.ArgumentParser(description="清理僵尸 RUNNING 的 pipeline run")
    ap.add_argument("--minutes", type=int, default=15, help="多久没更新算死（默认 15）")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只列出）")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    db = PipelineDB(args.db)
    stale = db.list_stale_runs(args.minutes)
    if not stale:
        print(json.dumps({"stale": [], "note": f"没有超过 {args.minutes} 分钟未更新的 RUNNING"},
                         ensure_ascii=False))
        return 0

    for r in stale:
        steps = r.get("steps_json") or "{}"
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            steps = {}
        stuck = [k for k, v in steps.items() if v == "RUNNING"]
        print(f"  {r['run_id']:24s} account={r['account'] or '-':22s} "
              f"updated_at={r['updated_at']}  卡在={stuck or '(未记录步骤)'}")

    if not args.apply:
        print(f"\n[DRY-RUN] 共 {len(stale)} 条，加 --apply 才会标记为 ABANDONED")
        return 0

    done = db.fail_stale_runs(args.minutes)
    print(f"\n[APPLIED] 已标记 ABANDONED: {', '.join(done)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
