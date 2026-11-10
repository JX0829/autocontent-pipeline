"""内置审批渠道 Provider 实现（feishu / cli）。

两者都只是把原有 `ask()` 包成 Provider，行为不变 —— 改造的是结构，不是语义。
"""
from __future__ import annotations

import os
import sys
from typing import Any

from .base import ApprovalProvider


class FeishuApprovalProvider(ApprovalProvider):
    """飞书审批：发卡片 → 轮询人工裁决 → 超时 reject。"""

    name = "feishu"

    def available(self) -> bool:
        return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))

    def unavailable_reason(self) -> str:
        return "feishu: 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET"

    def ask(self, run_id: str, artifacts: dict[str, Any], message: str) -> dict[str, Any]:
        from .feishu import ask  # 延迟导入：避免无 lark_oapi 环境 import 期就炸
        from .registry import profile_of

        # 渠道自己的配置（卡片样式/超时/是否展示发布矩阵）来自 pipeline.yaml 的 provider profile
        return ask(run_id, artifacts, message, profile=profile_of(self.name))


class CliApprovalProvider(ApprovalProvider):
    """终端审批：tty 交互；非 tty 必须显式 APPROVAL_NONINTERACTIVE，否则 fail closed。"""

    name = "cli"

    def available(self) -> bool:
        # 非 tty 只有在显式声明非交互裁决时才可用，否则视为不可用（不默认放行）
        return bool(sys.stdin.isatty() or os.getenv("APPROVAL_NONINTERACTIVE"))

    def unavailable_reason(self) -> str:
        return "cli: 非 tty 且未设置 APPROVAL_NONINTERACTIVE"

    def ask(self, run_id: str, artifacts: dict[str, Any], message: str) -> dict[str, Any]:
        from .cli import ask
        return ask(run_id, artifacts, message)
