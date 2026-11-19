#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""代码版本自愈回归（temp git 仓库，不联网、不碰真实 checkout）。

守四件事（2026-11-19 用户要求「保证每次修改小品都能用到最新的」）：
  T1 已最新        → action=none，不动 checkout
  T2 落后远端      → 自动 merge --ff-only 追平
  T3 工作区有改动  → 抛 STALE_CHECKOUT，绝不硬拉
  T4 PIPELINE_SKIP_SYNC=1 → 跳过同步
  T5 个人分支+未推提交 → 抛 STALE_CHECKOUT
  T6 个人分支+干净    → 自动切回 develop 并追平

跑法：python3 scripts/test_checkout_sync.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {p.stderr.strip()}")
    return p.stdout.strip()


def commit(repo: Path, name: str, msg: str) -> None:
    (repo / name).write_text(msg, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def main() -> int:
    from src.pipeline.code_sync import StaleCheckoutError, ensure_latest_code

    tmp = Path(tempfile.mkdtemp(prefix="syncchk-"))
    bare = tmp / "origin.git"
    seed = tmp / "seed"
    try:
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "develop", str(seed)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            git(seed, "config", k, v)
        commit(seed, "a.txt", "c1")
        git(seed, "remote", "add", "origin", str(bare))
        git(seed, "push", "-q", "-u", "origin", "develop")

        work = tmp / "work"
        subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            git(work, "config", k, v)
        # bare 仓库没有 HEAD 指向 → clone 后显式切 develop（否则是 unborn HEAD）
        git(work, "checkout", "-q", "-B", "develop", "origin/develop")

        # T1 已最新
        info = ensure_latest_code(verbose=False, repo=work)
        check("T1 已最新时不动", info["action"] == "none" and info["before"] == info["after"],
              str(info))

        # T2 落后 → 自动追平
        commit(seed, "b.txt", "c2")
        git(seed, "push", "-q", "origin", "develop")
        info = ensure_latest_code(verbose=False, repo=work)
        head = git(work, "rev-parse", "--short", "HEAD")
        remote = git(work, "rev-parse", "--short", "origin/develop")
        check("T2 落后自动 ff 追平", head == remote and info["action"] in ("pull", "none"),
              f"action={info['action']} head={head} remote={remote}")

        # T3 工作区有改动 → 拒绝
        commit(seed, "c.txt", "c3")
        git(seed, "push", "-q", "origin", "develop")
        (work / "dirty.txt").write_text("x", encoding="utf-8")
        git(work, "add", "dirty.txt")
        raised = ""
        try:
            ensure_latest_code(verbose=False, repo=work)
        except StaleCheckoutError as exc:
            raised = str(exc)
        check("T3 脏工作区拒绝硬拉", raised.startswith("STALE_CHECKOUT"), raised[:60])
        git(work, "reset", "-q", "--hard", "HEAD")
        (work / "dirty.txt").unlink(missing_ok=True)

        # T4 跳过开关
        os.environ["PIPELINE_SKIP_SYNC"] = "1"
        try:
            info = ensure_latest_code(verbose=False, repo=work)
        finally:
            os.environ.pop("PIPELINE_SKIP_SYNC", None)
        check("T4 跳过开关生效", info["action"].startswith("skip"), str(info))

        # T5 个人分支 + 未推提交 → 拒绝
        git(work, "checkout", "-q", "-b", "feat/local")
        commit(work, "local.txt", "local work")
        raised = ""
        try:
            ensure_latest_code(verbose=False, repo=work)
        except StaleCheckoutError as exc:
            raised = str(exc)
        check("T5 个人分支有未推提交 → 拒绝", raised.startswith("STALE_CHECKOUT"), raised[:60])

        # T6 个人分支 + 干净 → 自动切回 develop
        git(work, "checkout", "-q", "develop")
        git(work, "branch", "-q", "-D", "feat/local")
        git(work, "checkout", "-q", "-b", "feat/clean")
        info = ensure_latest_code(verbose=False, repo=work)
        branch = git(work, "rev-parse", "--abbrev-ref", "HEAD")
        check("T6 个人分支干净 → 切回 develop", branch == "develop",
              f"branch={branch} action={info['action']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"\n汇总: {passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
