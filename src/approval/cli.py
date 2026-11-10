"""cli 审批渠道（T4.6）：终端交互；非交互环境必须显式表态，否则 fail closed。"""
from __future__ import annotations

import os
import sys
from typing import Any


def ask(run_id: str, artifacts: dict[str, Any], message: str) -> dict[str, Any]:
    prompt = (f"\n[审批] {message}\n"
              f"  run_id = {run_id}\n"
              f"  产物   = {', '.join(sorted(artifacts)) or '(无)'}\n"
              f"  通过请输入 approve，拒绝请输入 reject: ")

    if sys.stdin.isatty():
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer in ("approve", "a", "y", "yes"):
            return {"decision": "approve", "decided_by": "cli_tty"}
        return {"decision": "reject", "decided_by": "cli_tty",
                "reason": f"终端输入={answer or '(空)'}"}

    # 非交互：不允许默认放行。必须由调用方显式声明本次放行/拒绝，
    # 这样 CI 能自动化，而"忘了配"不会变成静默通过。
    forced = (os.getenv("APPROVAL_NONINTERACTIVE") or "").strip().lower()
    if forced in ("approve", "reject"):
        print(f"  [approval] 非交互环境，按 APPROVAL_NONINTERACTIVE={forced} 裁决")
        return {"decision": forced, "decided_by": "env:APPROVAL_NONINTERACTIVE",
                "reason": "非交互环境显式声明"}
    print("  [approval] 非交互环境且未设置 APPROVAL_NONINTERACTIVE → 拒绝（fail closed）")
    return {"decision": "reject", "decided_by": "fail_closed",
            "reason": "非交互环境未显式声明审批结果（设置 APPROVAL_NONINTERACTIVE=approve|reject）"}
