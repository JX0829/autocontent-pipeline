"""审批渠道适配层（T4.6，v2 改造为 Provider 注册表）

对外接口保持不变，调用方（`exec_approval` / runner）不需要改：

    request_approval(run_id, artifacts, message, mode=None) -> dict
        {"decision": "approve"|"reject", "mode": <实际渠道>, "reason": str, "decided_by": str}

改造点：
  · 每个渠道是一个 Provider（`src/approval/providers.py`），自己声明 available()
  · 主流程不再 if/else 硬编码渠道，只认 `registry.resolve()` 选出的 provider
  · 新增渠道 = 实现 ApprovalProvider + `register()`，不改本文件
  · 显式指定 mode 但不可用 → 直接抛错（不偷偷降级）；auto 才按 feishu → cli 兜底
"""
from __future__ import annotations

from typing import Any

from .base import ApprovalProvider
from .providers import CliApprovalProvider, FeishuApprovalProvider
from .registry import (REGISTRY, UnknownProvider, get, list_providers, register,
                       resolve)

VALID_MODES = ("feishu", "cli", "auto")

# 内置渠道注册（override=True：模块重载时不报重复注册）
register(FeishuApprovalProvider(), override=True)
register(CliApprovalProvider(), override=True)


def resolve_mode(mode: str | None = None) -> tuple[str, str]:
    """兼容旧接口：返回 (实际渠道, 选择原因)。"""
    provider, why = resolve(mode)
    return provider.name, why


def request_approval(run_id: str, artifacts: dict[str, Any], message: str,
                     mode: str | None = None) -> dict[str, Any]:
    """发起审批。渠道由注册表解析，返回统一结构。"""
    provider, why = resolve(mode)
    print(f"  [approval] 渠道={provider.name}（{why}）")
    result = provider.ask(run_id, artifacts, message)
    result.setdefault("mode", provider.name)
    result.setdefault("reason", why)
    return result


__all__ = [
    "ApprovalProvider", "REGISTRY", "UnknownProvider", "VALID_MODES",
    "get", "list_providers", "register", "request_approval", "resolve", "resolve_mode",
]
