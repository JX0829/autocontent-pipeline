#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按「真能 import」挑解释器（B 站上传专用）。

背景：`scripts/bilibili_live_uploader.py` 依赖第三方 `bilibili_api`，而项目解释器
里没有（PEP 668 锁定装不进去）。`BilibiliProvider` 原先把它硬编码成
`~/.agent-reach/tools/bili-cli/venv/bin/python3` —— 那个目录一被清理，B 站发布
就 100% 失败，且被 provider 笼统包成 `BILI_UPLOAD_FAILED`（看不出是解释器问题）。

选法：`BILI_PYTHON` 优先，其次当前解释器，最后已知候选；**每个候选都真跑一次
`import bilibili_api`**，不靠路径存在与否判断。

    py, probes = find_interpreter("bilibili_api")
    if not py:  # probes 里带每个候选的失败原因，直接回给调用方
        ...
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent.parent

#: `bilibili_api` 在 pip 上的包名，用于提示
INSTALL_HINT = "uv pip install bilibili-api-python"

DEFAULT_CANDIDATES = (
    "~/.agent-reach/tools/bili-cli/venv/bin/python3",
    "/opt/homebrew/bin/python3.12",
    "/usr/local/bin/python3.12",
)


def candidate_interpreters() -> list[str]:
    """按优先级返回候选解释器绝对路径（去重，保序）。"""
    raw = [os.getenv("BILI_PYTHON") or "", sys.executable, *DEFAULT_CANDIDATES]
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not item:
            continue
        path = str(Path(item).expanduser())
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        out.append(path)
    return out


def find_interpreter(module: str = "bilibili_api", timeout: int = 30) -> tuple[str, list[dict]]:
    """返回 (可用解释器路径或 "", 每个候选的探测结果)。"""
    import subprocess

    probes: list[dict] = []
    for py in candidate_interpreters():
        try:
            proc = subprocess.run(
                [py, "-c", f"import {module}"],
                capture_output=True, text=True, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — 候选不可执行只是不可用，继续试下一个
            probes.append({"python": py, "ok": False, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if proc.returncode == 0:
            probes.append({"python": py, "ok": True, "reason": ""})
            return py, probes
        reason = (proc.stderr or proc.stdout or "").strip().splitlines()
        probes.append({"python": py, "ok": False,
                       "reason": reason[-1] if reason else f"exit={proc.returncode}"})
    return "", probes
