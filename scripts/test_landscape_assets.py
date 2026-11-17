#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横版衍生素材契约测试（方案 B，2026-11-17）。

判据：
  T1  有 `_landscape` 副本 → props 的 open/bg 被换成原生 16:9 副本；
  T2  没有副本 → 原样保留（回落竖版裁切），报告记 fallback；
  T3  入参 props 不被修改（横版替换不能污染竖版 props，两路是并行的）；
  T4  src 为空 / 指向不存在的文件 → 返回 None，不抛异常；
  T5  asset_binding 里落 landscape 审计（used/fallback 可回溯）；
  T6  路径分隔符统一为正斜杠（Remotion staticFile 只吃正斜杠）。

用法：python3 scripts/test_landscape_assets.py [-v]
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.assets_manager import (  # noqa: E402
    LANDSCAPE_SUFFIX,
    landscape_variant_src,
    with_landscape_assets,
)

PUBLIC = PROJ / "renderer" / "public"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


def main() -> int:
    # 找一个真实存在横版副本的竖版素材做样本
    samples = sorted((PUBLIC / "assets").rglob("*_landscape.mp4"))
    if not samples:
        print("SKIP：本机没有 *_landscape.mp4 副本，跳过 T1/T3/T5（不算失败）")
        has_copy = False
        vertical_src = "assets/tech/video/open/__not_exist__.mp4"
    else:
        has_copy = True
        land_rel = samples[0].relative_to(PUBLIC).as_posix()
        vertical_src = land_rel.replace(f"{LANDSCAPE_SUFFIX}.mp4", ".mp4")

    # T1 有副本 → 替换
    if has_copy:
        base = {"assets": {"open": vertical_src, "bg": vertical_src, "open_start_from": 3}}
        out, rep = with_landscape_assets(base)
        check("T1 有副本则替换为原生 16:9",
              out["assets"]["open"].endswith(f"{LANDSCAPE_SUFFIX}.mp4")
              and out["assets"]["bg"].endswith(f"{LANDSCAPE_SUFFIX}.mp4"),
              f"open={out['assets']['open']}")
        check("T1b 其余字段保持（open_start_from 未被改）",
              out["assets"]["open_start_from"] == 3,
              str(out["assets"].get("open_start_from")))
        # T3 入参不被污染
        check("T3 入参 props 未被修改（竖版 props 安全）",
              base["assets"]["open"] == vertical_src and base["assets"]["bg"] == vertical_src,
              base["assets"]["open"])
        # T5 审计
        check("T5 asset_binding 落 landscape 审计",
              rep["aspect"] == "landscape" and "open" in rep["used"],
              str(rep)[:120])
        # T6 正斜杠
        check("T6 路径统一正斜杠", "\\" not in out["assets"]["open"], out["assets"]["open"])

    # T2 无副本 → 回落
    fake = {"assets": {"open": "assets/tech/video/open/__no_such_asset__.mp4",
                       "bg": "assets/tech/video/bg/__no_such_asset__.mp4"}}
    out2, rep2 = with_landscape_assets(fake)
    check("T2 无副本则原样回落（不炸、不猜）",
          out2["assets"]["open"] == fake["assets"]["open"]
          and set(rep2["fallback"]) == {"open", "bg"},
          f"fallback={rep2['fallback']}")

    # T4 空值 / 不存在
    check("T4a 空 src 返回 None", landscape_variant_src("") is None)
    check("T4b 不存在的文件返回 None",
          landscape_variant_src("assets/tech/video/open/__nope__.mp4") is None)

    # T7 aspect 标记（渲染侧据此知道是横版 props）
    out3, _ = with_landscape_assets({"assets": {"open": vertical_src}})
    check("T7 landscape props 带 aspect 标记",
          out3["assets"].get("aspect") == "landscape", str(out3["assets"].get("aspect")))

    # T8 深层对象不被共享引用（深拷贝验证）
    deep = {"assets": {"open": vertical_src}, "scenes": [{"n": 1}]}
    deep_before = copy.deepcopy(deep)
    out4, _ = with_landscape_assets(deep)
    out4["scenes"][0]["n"] = 999
    check("T8 深拷贝：改横版 props 不回流竖版",
          deep == deep_before, str(deep["scenes"]))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"\n汇总: {passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
