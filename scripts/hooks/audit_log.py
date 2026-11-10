#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.audit_log（observe）—— 审计台账。

把每个步骤的关键信息（task_id / step / hook 结果 / 产物 / 时间）追加到
`<run_root>/audit.jsonl`，并同步到 `data/audit.jsonl`（跨 run 汇总）。
observe 型：写失败只报告，不阻断。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    record = {
        "ts": C.now_iso(),
        "task_id": p.get("task_id"),
        "run_root": p.get("run_root"),
        "step_id": p.get("step_id"),
        "hook": "hook.audit_log",
        "platform": c.get("platform"),
        "account": c.get("account"),
        "artifacts": sorted((p.get("artifacts") or {}).keys()),
        "outcome": c.get("outcome"),
    }

    written = []
    for target in (C.run_root(p) / "audit.jsonl", C.ledger_path("audit")):
        try:
            C.append_jsonl(target, record)
            written.append(str(target))
        except OSError as exc:
            C.fail(f"audit_log: 写入失败 {target}: {exc}")

    C.emit(True, patch={"audit_written": written})


if __name__ == "__main__":
    main()
