#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.material_check（gate, block）—— 事实底座校验。

比 material_bundle 更严：不仅要合 schema，还要保证"有事实可用"：
  · body 存在且 >= 20 字（否则只能靠 source_url）
  · source_url 必须是 http(s)
  · body 不能是纯占位串（如 lorem/测试/xxx）
不满足即 block —— 宁可不做，也不让模型凭空编造。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))
from src.pipeline.manifest import load_manifest, validate  # noqa: E402

PLACEHOLDER_RE = re.compile(r"(?i)^(lorem|test|测试|xxx|todo|占位|abc)+$")


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or C.read_json(C.artifact(p, "manifest"))
    if not isinstance(man, dict):
        C.fail("material_check: 缺 manifest")

    m = load_manifest(man)
    errors = validate(m)
    if errors:
        C.fail("material_check: 契约不通过 → " + "; ".join(errors[:4]))

    problems: list[str] = []
    if not m.body and not m.source_url:
        problems.append("body 与 source_url 均缺失")
    if m.source_url and not re.match(r"^https?://", m.source_url):
        problems.append(f"source_url 非 http(s): {m.source_url[:40]}")
    if m.body:
        if len(m.body) < 20:
            problems.append(f"body 仅 {len(m.body)} 字，不足以取材")
        if PLACEHOLDER_RE.match(m.body.strip()):
            problems.append("body 是占位串，无真实事实")
        if len(set(m.body)) < 6:
            problems.append("body 字符重复度过高，疑似占位")
    if problems:
        C.fail("material_check: " + "; ".join(problems), problems=problems)

    C.emit(True, patch={
        "material_ok": True,
        "facts_chars": len(m.body or "") + len(m.source_url or ""),
        "fact_summary_150": (m.body or "")[:150],
    })


if __name__ == "__main__":
    main()
