"""审批渠道 Provider 注册表。

新增渠道 = 写一个 ApprovalProvider 子类 + register()，不用改主流程。
"""
from __future__ import annotations

import os
from typing import Any

from .base import ApprovalProvider

REGISTRY: dict[str, ApprovalProvider] = {}


class UnknownProvider(ValueError):
    """请求了未注册/已禁用的审批渠道。"""


def register(provider: ApprovalProvider, *, override: bool = False) -> None:
    if provider.name in REGISTRY and not override:
        raise ValueError(f"审批渠道已注册: {provider.name}（如需替换请 override=True）")
    REGISTRY[provider.name] = provider


def get(name: str) -> ApprovalProvider:
    key = (name or "").strip().lower()
    if key not in REGISTRY:
        raise UnknownProvider(f"未注册的审批渠道: {key}（已注册: {sorted(REGISTRY)}）")
    return REGISTRY[key]


def list_providers() -> list[dict[str, Any]]:
    return [{"name": p.name, "available": p.available(),
             "reason": "" if p.available() else p.unavailable_reason()}
            for p in REGISTRY.values()]


# ---------------------------------------------------------------- 槽位配置
# pipeline.yaml 里 `providers.<slot>` 中 kind=approval 的 profile，就是该渠道的配置
# （卡片样式、超时、是否展示发布矩阵…）。这里按"渠道名"建立索引：
#     providers.approval.feishu.profile → profile_of("feishu")
_PROFILES: dict[str, dict[str, Any]] = {}
_PROFILES_LOADED = False


def load_slot_profiles(path=None) -> dict[str, dict[str, Any]]:
    """从 pipeline.yaml 读 kind=approval 的 provider profile（按渠道名索引，幂等）。"""
    global _PROFILES_LOADED
    if _PROFILES_LOADED and path is None:
        return _PROFILES
    from pathlib import Path

    cfg_path = path or (Path(__file__).resolve().parent.parent.parent / "pipeline.yaml")
    try:
        import yaml

        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — 配置读不到就用空 profile，不阻塞审批
        print(f"  [approval] 读取 {cfg_path} 失败，渠道按默认 profile 运行: {e}")
        cfg = {}

    profiles: dict[str, dict[str, Any]] = {}
    for slot, pcfg in (cfg.get("providers") or {}).items():
        if not isinstance(pcfg, dict) or pcfg.get("kind") != "approval":
            continue
        channel = str(slot).split(".", 1)[1] if "." in str(slot) else str(slot)
        profiles[channel.strip().lower()] = dict(pcfg.get("profile") or {})
    _PROFILES.clear()
    _PROFILES.update(profiles)
    _PROFILES_LOADED = True
    return _PROFILES


def profile_of(name: str) -> dict[str, Any]:
    """取某个渠道的 profile（未声明则空 dict，用内置默认值）。"""
    load_slot_profiles()
    return _PROFILES.get((name or "").strip().lower(), {})


def resolve(mode: str | None = None, *, fallback_chain: tuple[str, ...] = ("feishu", "cli")
            ) -> tuple[ApprovalProvider, str]:
    """返回 (provider, 选择原因)。

    · 显式 mode（非 auto）→ 直接取，不可用就抛（不偷偷降级）
    · auto / 未设 → 按 fallback_chain 找第一个可用的，并说明降级原因
    """
    wanted = (mode or os.getenv("APPROVAL_MODE") or "auto").strip().lower()
    if wanted != "auto":
        provider = get(wanted)
        if not provider.available():
            raise UnknownProvider(
                f"审批渠道 {wanted} 不可用：{provider.unavailable_reason()}（显式指定不降级）")
        return provider, f"显式指定 mode={wanted}"

    tried: list[str] = []
    for name in fallback_chain:
        provider = REGISTRY.get(name)
        if provider is None:
            tried.append(f"{name}(未注册)")
            continue
        if provider.available():
            why = "auto：命中首个可用渠道 " + name
            if tried:
                why += f"（跳过 {', '.join(tried)}）"
            return provider, why
        tried.append(f"{name}({provider.unavailable_reason()})")

    raise UnknownProvider("没有可用的审批渠道：" + "；".join(tried))
