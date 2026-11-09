"""feishu 审批渠道（T4.6）：发卡片 → 等人工裁决 → 超时即拒绝。

裁决写入方式（回调服务或人工都可以写）：
    data/approvals/<run_id>.json  =  {"decision": "approve"|"reject", "by": "...", "at": "..."}

超时策略：默认 600s，可用 APPROVAL_TIMEOUT_SEC 覆盖；超时 → reject（不静默放行）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent.parent
DECISION_DIR = PROJ / "data" / "approvals"


def _decision_path(run_id: str) -> Path:
    return DECISION_DIR / f"{run_id}.json"


def ask(run_id: str, artifacts: dict[str, Any], message: str) -> dict[str, Any]:
    from src.feishu.approval_service import FeishuApprovalService

    receive_id = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not receive_id:
        return {"decision": "reject", "decided_by": "config_missing",
                "reason": "缺 FEISHU_APPROVAL_CHAT_ID，无法发审批"}
    rec_type = "open_id" if receive_id.startswith("ou_") else "chat_id"

    dp = _decision_path(run_id)
    if dp.exists():
        dp.unlink()          # 清掉上一轮的残留裁决，避免"旧 approve 放行新一轮"

    svc = FeishuApprovalService()
    text = (f"【待审批】{message}\n"
            f"run_id: {run_id}\n"
            f"产物: {', '.join(sorted(artifacts)) or '(无)'}\n"
            f"请回复 approve / reject（裁决写入 {dp.relative_to(PROJ)}）")
    msg_id = svc.send_text_message(receive_id=receive_id, receive_id_type=rec_type, text=text)
    if not msg_id:
        return {"decision": "reject", "decided_by": "feishu_send_failed",
                "reason": "审批通知发送失败"}

    timeout = int(os.getenv("APPROVAL_TIMEOUT_SEC") or "600")
    deadline = time.time() + timeout
    print(f"  [approval] 已发飞书审批（msg_id={msg_id}），等待裁决，超时 {timeout}s")
    while time.time() < deadline:
        if dp.exists():
            try:
                data = json.loads(dp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(2)
                continue
            decision = str(data.get("decision") or "").lower()
            if decision in ("approve", "reject"):
                return {"decision": decision, "decided_by": f"feishu:{data.get('by', '?')}",
                        "feishu_msg_id": msg_id}
        time.sleep(2)

    return {"decision": "reject", "decided_by": "feishu_timeout",
            "reason": f"等待审批超时（{timeout}s），按拒绝处理", "feishu_msg_id": msg_id}
