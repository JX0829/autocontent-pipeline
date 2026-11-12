#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号配置唯一入口（T4.2 去硬编码）

之前账号 ID（形如 `<platform>-<tier>-<name>`）被硬编码在 8 处代码里：换账号要改源码，
而账号是**配置**不是代码。这里收口成一个只读加载器：

    load_accounts()                  → [ {id, platform, enabled, ...}, ... ]
    by_id(account_id)                → dict | None
    for_platform("douyin")           → 首个 enabled 的抖音账号
    default_account()                → 首个 enabled 账号的 id

配置缺失或为空 → 抛 AccountConfigError（**不给默认值兜底**，缺就是缺）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = PROJ / "config" / "accounts.yaml"


class AccountConfigError(RuntimeError):
    """账号配置不可用（缺失 / 为空 / 平台下无启用账号）。"""


def load_accounts(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        raise AccountConfigError(f"账号配置不存在: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AccountConfigError(f"账号配置解析失败: {p}（{exc}）") from exc
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list) or not accounts:
        raise AccountConfigError(f"账号配置为空: {p}")
    return [a for a in accounts if isinstance(a, dict) and a.get("id")]


def by_id(account_id: str, path: str | Path | None = None) -> dict[str, Any] | None:
    return next((a for a in load_accounts(path) if a["id"] == account_id), None)


def for_platform(platform: str, account_type: str | None = None,
                 path: str | Path | None = None) -> dict[str, Any]:
    """取该平台第一个启用账号；没有则抛错（不静默返回空 dict）。

    account_type 缺省（None）= 旧行为：只按平台取首个启用账号。
    给出 account_type 时**严格按账号类型匹配**，宁可报错也不串号：
    课文故事（edu）绝不能落到科技号，科技速报也绝不能落到教育号
    （F-2026-11-12 §11.3 缺口4 / 2026-11-12 教育号落地方案）。
    """
    cands = [a for a in load_accounts(path)
             if str(a.get("platform") or "") == platform and a.get("enabled", True)]
    if account_type:
        typed = [a for a in cands
                 if str(a.get("account_type") or "") == str(account_type)]
        if not typed:
            enabled_types = sorted({str(a.get("account_type") or "") for a in cands}) or ["(无)"]
            raise AccountConfigError(
                f"平台 {platform} 下没有启用的 {account_type} 账号（config/accounts.yaml）；"
                f"该平台现有启用类型: {', '.join(enabled_types)}")
        return typed[0]
    if not cands:
        raise AccountConfigError(f"平台 {platform} 下没有启用的账号（config/accounts.yaml）")
    return cands[0]


def default_account(path: str | Path | None = None) -> str:
    enabled = [a for a in load_accounts(path) if a.get("enabled", True)]
    if not enabled:
        raise AccountConfigError("没有任何启用的账号（config/accounts.yaml）")
    return str(enabled[0]["id"])


__all__ = ["AccountConfigError", "load_accounts", "by_id", "for_platform", "default_account"]
