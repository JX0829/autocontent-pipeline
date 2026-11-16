#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""RUNBOOK 命令自检：文档里写的每条命令，脚本在不在、参数在不在。

背景（2026-11-15）：「Runbook 能不能照着跑」以前只能靠人肉试。收口视频号时发现
RUNBOOK 还在教人用已删除的 `scripts/publish_channels.py`、还在用裸 `python3`
（指向 conda、缺项目依赖）—— 文档漂移没人拦。这个脚本把「文档 ↔ 代码」钉在一起：

  · 解析 RUNBOOK.md 里的 ```bash 代码块
  · 每条命令解析出「脚本/module + --flags」
  · 脚本必须存在；每个 `--flag` 必须在该脚本源码里出现（argparse 定义处）
  · 裸 `python3` 调用 `-m src.*` / `scripts/*.py` → 判违规（必须用 $PY）

不执行任何命令（不联网、不渲染、不发布）。

用法：python3.14 scripts/verify_runbook.py [--verbose]
退出码：0 全绿 / 1 有漂移
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
RUNBOOK = PROJ / "RUNBOOK.md"

PLACEHOLDER = re.compile(r"[<…]|\.\.\.|/path/to|materials/<")
CMD = re.compile(r"^(?P<py>\$PY|python3|python3\.\d+)\s+(?P<rest>.+)$")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]+)")
MODULE = re.compile(r"-m\s+(?P<mod>[\w.]+)")
SCRIPT = re.compile(r"(?P<script>scripts/[\w./-]+\.(?:py|sh))")


def commands(text: str) -> list[tuple[int, str]]:
    """抽出 bash 代码块里的命令，返回 (行号, 命令)。

    `\\` 续行拼成一条；其它每行都是独立命令（同一块里常有多条，不能整块拼成一条 ——
    否则参数会张冠李戴：`--build-only` 被算到上一行的 orchestrator 头上）。
    """
    out: list[tuple[int, str]] = []
    in_block, buf, start = False, "", 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            if in_block and buf.strip():
                out.append((start, buf.strip()))
            in_block = line.strip() == "```bash"
            buf, start = "", 0
            continue
        if not in_block:
            continue
        stripped = line.rstrip()
        if not stripped.strip() or stripped.strip().startswith("#"):
            continue
        if not buf:
            start = lineno
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
            continue
        buf += stripped.strip()
        out.append((start, buf.strip()))
        buf = ""
    if buf.strip():
        out.append((start, buf.strip()))
    return out


def check_cmd(lineno: int, cmd: str) -> list[str]:
    problems: list[str] = []
    m = CMD.match(cmd)
    if not m:
        return problems
    if m.group("py") in ("python3", "python3.11", "python3.12", "python3.13"):
        if "-m src." in cmd or "scripts/" in cmd:
            problems.append(f"RUNBOOK.md:{lineno} 用了裸 `{m.group('py')}`（会命中 conda 解释器）"
                            f"，改成 $PY：{cmd[:80]}")

    target_src: Path | None = None
    sm = SCRIPT.search(cmd)
    mod = MODULE.search(cmd)
    if sm:
        target_src = PROJ / sm.group("script")
        if not target_src.exists():
            problems.append(f"RUNBOOK.md:{lineno} 引用的脚本不存在：{sm.group('script')}")
            target_src = None
    elif mod:
        mod_name = mod.group("mod")
        if mod_name.startswith("src."):
            rel = mod_name.replace(".", "/") + ".py"
            cand = PROJ / rel
            if not cand.exists():
                cand = PROJ / rel.replace(".py", "/__main__.py")
            target_src = cand if cand.exists() else None
            if target_src is None:
                problems.append(f"RUNBOOK.md:{lineno} 引用的模块不存在：{mod_name}")

    if target_src and PLACEHOLDER.search(cmd) is None:
        src = target_src.read_text(encoding="utf-8", errors="ignore")
        for flag in dict.fromkeys(FLAG.findall(cmd)):
            if flag in ("--help", "--force"):
                continue
            if flag not in src:
                problems.append(f"RUNBOOK.md:{lineno} `{target_src.name}` 没有参数 {flag}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not RUNBOOK.exists():
        print("找不到 RUNBOOK.md")
        return 1

    cmds = commands(RUNBOOK.read_text(encoding="utf-8"))
    problems: list[str] = []
    for lineno, cmd in cmds:
        found = check_cmd(lineno, cmd)
        if args.verbose and not found:
            print(f"  ok  L{lineno}: {cmd[:90]}")
        problems.extend(found)

    print("=== RUNBOOK 命令自检 ===")
    print(f"  解析命令 {len(cmds)} 条")
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        print(f"RUNBOOK_DRIFT {len(problems)} 处")
        return 1
    print("  脚本存在 / 参数存在 / 解释器显式 —— 全部对上")
    print("RUNBOOK_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
