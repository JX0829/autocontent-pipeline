#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.9 配置面巡检（spec_enhancement.md §5 T1.9）

三面比对，输出两个关键指标：
    DEADKEY   —— .env 里写着，但代码不读、也没在 pipeline.yaml 声明（死键）
    UNDECLARED —— 代码在读，但 pipeline.yaml 未声明（必填或可选都没写）

用法：
    python3 scripts/audit_config.py           # 人类可读
    python3 scripts/audit_config.py --json    # 机器可读（CI）

退出码：0 干净 / 1 有 DEADKEY 或 UNDECLARED
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.pipeline.config import audit  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="配置面巡检（T1.9）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    res = audit()

    if args.json:
        print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    else:
        print("=== 配置面巡检 ===")
        print(f"  声明键 (pipeline.yaml) : {len(res.declared)}")
        print(f"  必填 / 可选            : {len(res.required)} / {len(res.optional)}")
        print(f"  .env 实际键            : {len(res.env_file)}")
        print(f"  代码实读键             : {len(res.code_read)}")
        print()
        print(f"  DEADKEY={len(res.dead_keys)} UNDECLARED={len(res.undeclared)} "
              f"MISSING_REQUIRED={len(res.missing_required)}")
        for k in res.dead_keys:
            print(f"    ✗ 死键（无人读）: {k}")
        for k in res.undeclared:
            print(f"    ✗ 未声明（代码在读）: {k}")
        for k in res.missing_required:
            print(f"    ✗ 必填但未设置: {k}")
        if res.ok:
            print("\n  结论：配置面干净 —— 无死键、无未声明、必填齐全")

    clean = not res.dead_keys and not res.undeclared
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
