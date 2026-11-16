#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""视频号/抖音「不得无头」巡检。

背景（2026-11-15）：`channels_session.ensure()` 拉起的是**常驻** Chrome。只要有一处
代码用 `headless=True` 把它拉起来，这个常驻进程就一直是无头的，后面所有发布都复用它
——「改成有头」这件事会静默失效。踩过的具体位置：

  * `channels_cdp.check_login_status()` ：`ensure(headless=True)`  → 探活就把实例拉成无头
  * `channels_cdp.publish_live(headless=True)`：默认值 True
  * `executors._invoke_provider()`：`opts.get("headless", True)` → 预检路径无头

本巡检扫「渠道发布链路」的文件与 pipeline.yaml，出现无头写法即 FAIL。

判据：
  Python：`headless=True` / `headless: bool = True` / `headless = True`
  YAML  ：`headless: true`（应显式写 false）
忽略：注释行（# 之后）与三引号文档串里的说明文字。

用法：python3.14 scripts/audit_channels_headful.py
退出码：0 全绿 / 1 发现无头写法
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

# 渠道发布链路（谁拉起浏览器、谁传 headless）
TARGETS = [
    "src/providers/channels_cdp.py",
    "src/providers/channels_session.py",
    "src/pipeline/executors.py",
    "scripts/channels_login.py",
    "scripts/test_channels_session.py",
    "scripts/probe_channels.py",
]

PY_BAD = [
    re.compile(r"headless\s*=\s*True\b"),
    re.compile(r"headless\s*:\s*bool\s*=\s*True\b"),
]
YAML_BAD = re.compile(r"^\s*headless\s*:\s*true\b", re.IGNORECASE)


def strip_comments_and_docstrings(text: str) -> list[tuple[int, str]]:
    """去掉 # 注释行与三引号块内的内容，返回 [(行号, 代码行)]。"""
    out: list[tuple[int, str]] = []
    in_doc = False
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw
        quotes = line.count('"""') + line.count("'''")
        if quotes % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        code = line.split("#", 1)[0]
        if code.strip():
            out.append((i, code))
    return out


def main() -> int:
    violations: list[str] = []

    for rel in TARGETS:
        path = PROJ / rel
        if not path.exists():
            continue
        for lineno, code in strip_comments_and_docstrings(path.read_text(encoding="utf-8")):
            for pat in PY_BAD:
                if pat.search(code):
                    violations.append(f"{rel}:{lineno}  {code.strip()[:90]}")
                    break

    yaml_targets = [PROJ / "pipeline.yaml", PROJ / "config" / "accounts.yaml"]
    for yaml_path in yaml_targets:
        if not yaml_path.exists():
            continue
        for lineno, code in strip_comments_and_docstrings(yaml_path.read_text(encoding="utf-8")):
            if YAML_BAD.search(code):
                violations.append(f"{yaml_path.relative_to(PROJ)}:{lineno}  {code.strip()[:90]}")

    print("=== 渠道无头巡检 ===")
    if violations:
        print(f"  ✗ 发现 {len(violations)} 处无头写法（视频号/抖音必须 headless=false）：")
        for v in violations:
            print(f"    - {v}")
        print("CHANNELS_HEADFUL_FAIL")
        return 1

    print(f"  扫描 {len([t for t in TARGETS if (PROJ / t).exists()])} 个文件 + pipeline.yaml/accounts.yaml")
    print("  未发现 headless=True / headless: true")
    print("CHANNELS_HEADFUL_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
