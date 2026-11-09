#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.material_bundle（transform）—— 物料打包进提示。

把整份 manifest 用 `Manifest.to_prompt_block()` 打成 <material> 块，并附「严禁编造」硬约束。
不再由调用方预拆 15 字段（T1.7 的核心要求）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))
from src.pipeline.manifest import load_manifest, validate  # noqa: E402


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or C.read_json(C.artifact(p, "manifest"))
    if not isinstance(man, dict):
        C.fail("material_bundle: 缺 manifest（context.manifest / artifacts.manifest）")

    m = load_manifest(man)
    errors = validate(m)
    if errors:
        C.fail("material_bundle: 物料不合契约 → " + "; ".join(errors[:4]))

    block = m.to_prompt_block()
    C.emit(True, patch={
        "material_block": block,
        "material_chars": len(block),
        "has_body": bool(m.body),
        "has_source_url": bool(m.source_url),
    })


if __name__ == "__main__":
    main()
