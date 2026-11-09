#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.provenance_check（gate, block）—— 事实溯源。

把 draft 正文里的**数字/版本号/参数**全部抽出来，逐个回查物料原文。
物料里找不到的数字 = 模型编造的，直接 block（这是 QA **M1「事实幻觉」** 的机器闸门）。

放行例外（避免误杀）：纯序号（1. 2. 3.）、年份 20xx 之外的常见计数词、以及
出现在 `context.allow_numbers` 白名单里的数字。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|GB|MB|TB|B|亿|万|千|倍|核|nm|mm|W|℃|ms|s|小时|分钟|天|年|个|种|家|款)?")


def _numbers(text: str) -> set[str]:
    return {m.group(0).strip() for m in NUM_RE.finditer(text)}


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script")) or {}

    body = str(draft.get("body") or draft.get("narration") or "")
    if len(body) < 20:
        C.fail(f"provenance_check: draft 正文过短（{len(body)} 字），无法溯源")

    source = (str(man.get("body") or "") + "\n" + str(man.get("title") or "") + "\n"
              + str(man.get("source_url") or "") + "\n" + C.text_of(c.get("facts")))
    if len(source.strip()) < 20:
        C.fail("provenance_check: 物料原文缺失，无法验证任何数字（先跑 material_check）")

    allowed = {str(x) for x in (c.get("allow_numbers") or [])}
    src_nums = _numbers(source)
    src_norm = C.normalize_text(source)

    fabricated: list[str] = []
    for num in sorted(_numbers(body)):
        if num in allowed or num in src_nums:
            continue
        # 数字本身（去掉单位）出现在原文里也算有据
        bare = re.sub(r"[^\d.]", "", num)
        if bare and (bare in src_nums or bare in src_norm):
            continue
        fabricated.append(num)

    if fabricated:
        C.fail("provenance_check: 正文含物料中不存在的数字 → " + ", ".join(fabricated[:8]),
               fabricated=fabricated[:20])

    C.emit(True, patch={"provenance": "ok", "checked_numbers": len(_numbers(body))})


if __name__ == "__main__":
    main()
