"""审批渠道适配层（T4.6）

统一接口：
    request_approval(run_id, artifacts, message, mode=None) -> dict
        {"decision": "approve"|"reject", "mode": <实际使用的渠道>, "reason": str, "decided_by": str}

为什么要有这一层：`approval` 步原先硬编码飞书卡片文案，本地/CI 手跑无法审批，
只能假装通过。适配层把"找谁审"从流程里剥出来，runner 只认 decision。

三种渠道：
  · feishu —— 发审批卡片，轮询 data/approvals/<run_id>.json 等人工裁决，超时即 reject
  · cli    —— 终端交互；非 tty 时必须显式给 APPROVAL_NONINTERACTIVE，否则 fail closed
  · auto   —— 有飞书凭据走 feishu，否则降级 cli，并打印降级原因（不静默）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent.parent
DECISION_DIR = PROJ / "data" / "approvals"

VALID_MODES = ("feishu", "cli", "auto")


def _has_feishu_creds() -> bool:
    return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))


def resolve_mode(mode: str | None = None) -> tuple[str, str]:
    """返回 (实际渠道, 降级/选择原因)。"""
    mode = (mode or os.getenv("APPROVAL_MODE") or "auto").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"APPROVAL_MODE 非法: {mode}（可选 {VALID_MODES}）")
    if mode != "auto":
        return mode, f"显式指定 mode={mode}"
    if _has_feishu_creds():
        return "feishu", "auto：检测到 FEISHU_APP_ID/SECRET，走飞书审批"
    return "cli", "auto：未配置飞书凭据，降级为 cli 审批"


def request_approval(run_id: str, artifacts: dict[str, Any], message: str,
                     mode: str | None = None) -> dict[str, Any]:
    channel, why = resolve_mode(mode)
    print(f"  [approval] 渠道={channel}（{why}）")
    if channel == "feishu":
        from src.approval.feishu import ask as ask_feishu
        result = ask_feishu(run_id, artifacts, message)
    else:
        from src.approval.cli import ask as ask_cli
        result = ask_cli(run_id, artifacts, message)
    result.setdefault("mode", channel)
    result.setdefault("reason", why)
    return result
