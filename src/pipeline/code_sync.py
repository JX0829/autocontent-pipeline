#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""代码版本自愈：保证「谁跑都是最新代码」。

背景（2026-11-19 用户要求「保证每次修改小品都能用到最新的」）：
  流水线跑在**共享主 checkout** 上，谁临时 checkout 了个人分支忘了切回来，
  后面的人出片就吃旧代码 —— 之前「改了随机模板但小品出片还是老样子」就是这么来的。

策略（fail-closed，宁可报错也不悄悄跑旧代码）：
  1. `PIPELINE_SKIP_SYNC=1` 或 `--no-sync`  → 完全跳过
  2. 分支不是 develop/main 且工作区干净    → 自动切 develop
  3. 落后 origin/develop 且工作区干净      → `git merge --ff-only` 自动追平
  4. 工作区有未提交改动                    → 抛 StaleCheckoutError，不硬拉别人的活
  5. 有未推送提交（与远端分叉/个人分支）   → 抛 StaleCheckoutError，交给人处理

所有 git 操作都带超时；fetch 失败（离线）只告警不拦，避免把出片卡死。

⚠️ 注意：本模块是 **code_sync**，与 `src/pipeline/preflight.py`（发布前置登录态预检）不是一回事。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
TRACK_BRANCH = "develop"
SUPPORTED_BRANCHES = ("develop", "main")

_CWD: Path = PROJ


class StaleCheckoutError(RuntimeError):
    """代码不是最新的，且不允许自动修复。"""


def _git(*args: str, timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(_CWD), capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _rev(spec: str) -> str:
    rc, out = _git("rev-parse", "--short", spec)
    return out if rc == 0 else "?"


def _is_ancestor(a: str, b: str) -> bool:
    rc, _ = _git("merge-base", "--is-ancestor", a, b)
    return rc == 0


def _dirty() -> bool:
    rc, out = _git("status", "--porcelain", "--untracked-files=no")
    return rc != 0 or bool(out.strip())


def _has_unpushed() -> bool:
    rc, out = _git("log", "--oneline", f"origin/{TRACK_BRANCH}..HEAD")
    return rc == 0 and bool(out.strip())


def ensure_latest_code(verbose: bool = True, repo: Path | str | None = None) -> dict[str, str]:
    """让当前 checkout 追平 origin/develop；不可安全修复时抛 StaleCheckoutError。

    `repo` 仅供测试注入（默认项目根），生产调用不用传。
    """
    global _CWD
    if repo is not None:
        _CWD = Path(repo)
    info: dict[str, str] = {"before": _rev("HEAD"), "after": "", "action": "none"}

    if os.environ.get("PIPELINE_SKIP_SYNC") == "1":
        info["action"] = "skip(PIPELINE_SKIP_SYNC=1)"
        info["after"] = info["before"]
        if verbose:
            print(f"[preflight] 已按要求跳过代码同步（{info['before']}）")
        return info

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")[1]
    rc, _ = _git("fetch", "origin", TRACK_BRANCH, timeout=25)
    fetch_ok = rc == 0
    target = _rev(f"origin/{TRACK_BRANCH}")

    if not fetch_ok:
        info["action"] = "fetch-failed(离线，沿用本地)"
        info["after"] = info["before"]
        if verbose:
            print(f"[preflight] ⚠️ 拉不到远端（{target}），沿用本地 {info['before']} 继续")
        return info

    if branch in SUPPORTED_BRANCHES and _is_ancestor(target, "HEAD"):
        info["after"] = info["before"]
        if verbose:
            print(f"[preflight] ✅ 代码已是最新（{branch}@{info['before']}）")
        return info

    if _dirty():
        raise StaleCheckoutError(
            f"STALE_CHECKOUT：工作区有未提交改动，代码停在 {info['before']}（远端 {target}）。\n"
            f"  不自动硬拉，避免冲掉别人正在改的东西。请先 commit/stash，再重跑。\n"
            f"  查看： git -C {_CWD} status")

    if branch not in SUPPORTED_BRANCHES:
        if _has_unpushed():
            raise StaleCheckoutError(
                f"STALE_CHECKOUT：当前在个人分支 {branch}（{info['before']}）且有未推送提交，"
                f"不敢自动切。\n  个人分支请用 worktree 跑，或先把提交推到远端再重跑。")
        _git("checkout", "-q", TRACK_BRANCH)
        info["action"] = f"switch({branch}→{TRACK_BRANCH})"
        branch = TRACK_BRANCH
    else:
        info["action"] = "pull"

    if _has_unpushed():
        raise StaleCheckoutError(
            f"STALE_CHECKOUT：{branch} 有未推送提交，与远端 {target} 分叉，拒绝自动合并。")

    rc, out = _git("merge", "--ff-only", f"origin/{TRACK_BRANCH}", timeout=60)
    if rc != 0:
        raise StaleCheckoutError(f"STALE_CHECKOUT：git merge --ff-only 失败：{out}")

    info["after"] = _rev("HEAD")
    if verbose:
        print(f"[preflight] 🔄 已自动同步代码 {info['before']} → {info['after']}"
              f"（{info['action']}，{branch}）")
    return info


__all__ = ["ensure_latest_code", "StaleCheckoutError"]
