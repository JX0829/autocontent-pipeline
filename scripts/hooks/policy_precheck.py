#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.policy_precheck（gate, block）—— 平台策略前置校验。

读 `config/platform_policies.yaml`：
  · `freeze_until` 未到期 → block（冷冻期内不许自动生成/发布）
  · `status: banned|disabled` → block
  · 目标平台不存在 → block（不猜平台）

账号 → 平台的推断规则：账号 ID 前缀（`<platform>-<tier>-<name>` → `<platform>`）。
"""
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def _policies() -> dict:
    f = C.PROJ / "config" / "platform_policies.yaml"
    if not f.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _platform_of(account: str) -> str:
    acc = (account or "").lower()
    # 2026-11-09 小红书整体下线：xhs / xiaohongshu 不再作为已知平台（映射到它们等于死映射）
    for known in ("douyin", "bilibili", "wechat", "kuaishou"):
        if acc.startswith(known):
            return known
    return acc.split("-")[0] if acc else ""


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    account = str(c.get("account") or "")
    platform = str(c.get("platform") or _platform_of(account))
    if not platform:
        C.fail("policy_precheck: 无法确定目标平台（context.account 为空）")

    policies = _policies().get("platforms") or {}
    # 账号前缀可能对不上平台键，做一次宽松匹配
    key = platform if platform in policies else next(
        (k for k in policies if platform and (platform in k or k in platform)), "")
    if not key:
        C.fail(f"policy_precheck: 平台 {platform} 未在 platform_policies.yaml 中登记")

    pol = policies[key] or {}
    status = str(pol.get("status") or "").lower()
    if status in ("banned", "disabled", "suspended"):
        C.fail(f"policy_precheck: 平台 {key} 当前状态 {status}，禁止自动出片/发布")

    freeze = pol.get("freeze_until")
    if freeze:
        try:
            frozen_until = datetime.strptime(str(freeze), "%Y-%m-%d").date()
        except ValueError:
            C.fail(f"policy_precheck: freeze_until 格式非法 {freeze!r}（应 YYYY-MM-DD）")
        if frozen_until >= date.today():
            C.fail(f"policy_precheck: 平台 {key} 处于冷冻期（至 {freeze}），禁止自动操作")

    C.emit(True, patch={
        "platform": key,
        "policy_status": status or "active",
        "freeze_until": freeze or None,
        "reply_mode": (pol.get("interaction_policy") or {}).get("reply_mode"),
    })


if __name__ == "__main__":
    main()
