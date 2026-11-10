#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.envelope_validate（observe）—— 投料信封完整性。

只做「信封」层面的检查：payload 结构与 manifest 基本形态。业务合法性归 material_check。
observe 型：即使发现问题也只报告，不阻断流水线（但会进日志）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def main() -> None:
    p = C.load_payload()
    problems: list[str] = []

    for key in ("task_id", "run_root", "step_id"):
        if not p.get(key):
            problems.append(f"payload 缺 {key}")

    man = C.ctx(p).get("manifest") or C.read_json(C.artifact(p, "manifest"))
    if not isinstance(man, dict):
        problems.append("manifest 未随 payload 提供（context.manifest / artifacts.manifest）")
    else:
        if not str(man.get("title") or "").strip():
            problems.append("manifest.title 为空")
        if not (man.get("body") or man.get("source_url")):
            problems.append("manifest 缺 body 与 source_url（anyOf 至少一个）")

    if problems:
        C.fail("envelope_invalid: " + "; ".join(problems))

    C.emit(True, {"envelope_ok": True, "carrier_hint": C.ctx(p).get("carrier", "unknown")})


if __name__ == "__main__":
    main()
