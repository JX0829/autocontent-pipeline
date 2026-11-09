#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.silence_guard（gate, warn）—— 静默失败哨兵。

QA 的 M4 是最难发现的一类：流程"成功"结束，但什么都没产出。
本 hook 在关键步骤后检查三件事，任一成立即 warn（on_fail: warn）并留 degraded 记录：
  1. 上游声明的 artifact 一个都不在
  2. context 里 `degraded_count` > 0（上游有降级动作）
  3. draft/narration 为空串或占位内容
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    a = C.arts(p)

    problems: list[str] = []
    present = []
    for key, val in a.items():
        path = C.artifact(p, key)
        if path is not None and path.exists() and path.stat().st_size > 0:
            present.append(key)
    if a and not present:
        problems.append(f"声明了 {len(a)} 个产物，但磁盘上一个都不存在")

    degraded = int(c.get("degraded_count") or 0)
    if degraded > 0:
        problems.append(f"上游发生 {degraded} 次降级（degraded_count>0）")

    draft = c.get("draft") or {}
    body = str((draft or {}).get("body") or "")
    if draft and len(body.strip()) < 20:
        problems.append(f"draft.body 仅 {len(body.strip())} 字，疑似空产出")

    if problems:
        C.fail("silence_guard: " + "; ".join(problems), artifacts_present=present)

    C.emit(True, patch={"silence_guard": "ok", "artifacts_present": len(present)})


if __name__ == "__main__":
    main()
