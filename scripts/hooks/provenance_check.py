#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.provenance_check（gate, block）—— 产物来源与事实溯源（T2.3 / QA #M1）

两层判据：

A. **产物来源（T2.3，dispatch 前必查）**
   待发产物必须位于**本次 run_root** 内。命中 `output/<历史日期>/...` 一律
   `{ok:false, reason:"stale_artifact"}` —— 这是"拿昨天那条片子重发一遍"的机械闸门。

B. **事实溯源（M1 事实幻觉）**
   若同时给了 draft 与 manifest：正文里的每个数字都必须能在物料原文中找到，
   找不到 = 模型编造 → block。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))

import re  # noqa: E402

NUM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|GB|MB|TB|B|亿|万|千|倍|核|nm|mm|W|℃|ms|s|小时|分钟|天|年|个|种|家|款)?")


def _is_stale(path: Path, run_root: Path) -> bool:
    """产物是否来自历史日期目录（不是本次 run）。"""
    try:
        path.relative_to(run_root)
        return False
    except ValueError:
        pass
    # 不在本次 run_root 内：若位于 output/<日期> 结构下，判为陈旧产物
    parts = path.parts
    if "output" in parts:
        i = parts.index("output")
        if len(parts) > i + 2 and re.fullmatch(r"\d{8}", parts[i + 1] or ""):
            return True
        return True  # output 下但不在本次 run_root：一律视为外部产物
    return False


def _numbers(text: str) -> set[str]:
    return {m.group(0).strip() for m in NUM_RE.finditer(text)}


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    root = C.run_root(p).resolve()

    # ---------- A. 产物来源 ----------
    checked: list[str] = []
    for key in (p.get("artifacts") or {}):
        path = C.artifact(p, str(key))
        if path is None:
            continue
        if _is_stale(path.resolve(), root):
            C.fail(f"stale_artifact: {key} 不在本次 run_root 内 → {path}",
                   artifact=key, path=str(path), run_root=str(root))
        checked.append(key)

    # ---------- B. 事实溯源（仅当提供 draft + manifest 时）----------
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script")) or {}
    body = str(draft.get("body") or draft.get("narration") or "") if isinstance(draft, dict) else ""

    fabricated: list[str] = []
    if body and (man.get("body") or man.get("source_url")):
        source = "\n".join(str(x) for x in (
            man.get("title"), man.get("body"), man.get("source_url")) if x)
        source += "\n" + C.text_of(c.get("facts"))
        allowed = {str(x) for x in (c.get("allow_numbers") or [])}
        src_nums = _numbers(source)
        src_norm = C.normalize_text(source)
        for num in sorted(_numbers(body)):
            if num in allowed or num in src_nums:
                continue
            bare = re.sub(r"[^\d.]", "", num)
            if bare and (bare in src_nums or bare in src_norm):
                continue
            fabricated.append(num)
        if fabricated:
            C.fail("provenance_check: 正文含物料中不存在的数字 → " + ", ".join(fabricated[:8]),
                   fabricated=fabricated[:20])

    C.emit(True, patch={
        "provenance": "ok",
        "artifacts_checked": len(checked),
        "stale": 0,
        "checked_numbers": len(_numbers(body)) if body else 0,
    })


if __name__ == "__main__":
    main()
