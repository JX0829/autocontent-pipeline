"""审批渠道 Provider 基类（T4.6 改造：适配层 → Provider 注册表）

改造前：`src/approval/__init__.py` 里 `if channel == "feishu": ... else: ...`，
新增一个渠道就要改主流程，且没法在不改代码的情况下替换实现。

改造后：每个渠道是一个 Provider，自己声明「可不可用」和「怎么问」，
主流程只认 `registry.get(mode).ask(...)` 返回的 decision。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ApprovalProvider(ABC):
    """审批渠道契约。

    实现者只需要管两件事：
      1. available() —— 当前环境能不能用这个渠道（凭据/环境变量/tty）
      2. ask()       —— 发起审批并返回统一结构
    """

    name: str = "base"

    @abstractmethod
    def available(self) -> bool:
        """当前环境是否可用（凭据齐、终端可交互等）。"""

    @abstractmethod
    def ask(self, run_id: str, artifacts: dict[str, Any], message: str) -> dict[str, Any]:
        """发起审批。返回 {"decision": "approve"|"reject", "decided_by": str, ...}。"""

    # ---- 约定：Provider 可覆盖 unavailable_reason() 说明为什么不可用 ----
    def unavailable_reason(self) -> str:
        return f"{self.name}: 环境不满足"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} name={self.name} available={self.available()}>"
