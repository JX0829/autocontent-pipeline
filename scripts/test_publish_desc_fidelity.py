#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文保真回归（2026-11-22）：正文原样下发，禁止二次改写。

事故：物料里 `+26%` / `-32.7%` 上线变成「涨了三成多」；
      日志里回读不一致也照发（视频号只判「长度 ≥10」）。
"""
import json
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.utils.video_desc import (DescFidelityError, check_desc_fidelity,  # noqa: E402
                                 material_body_desc, numeric_tokens)

PASS = 0
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}" + (f" | {extra}" if extra else ""))
    else:
        FAIL.append(name)
        print(f"FAIL  {name}" + (f" | {extra}" if extra else ""))


def read(p: str) -> str:
    return (PROJ / p).read_text(encoding="utf-8")


def main() -> int:
    src = "FSD v15 参数扩到 100 亿，反应快 20%，内存 -32.7%。"

    # ① 数字保真
    check("① 原样正文通过", check_desc_fidelity(src, src) == [])
    bad = "FSD v15 参数扩到一百亿，反应涨了三成多，内存降了三成。"
    miss = check_desc_fidelity(src, bad)
    check("① 改写数字被拦（缺 100 亿/20%/-32.7%）", len(miss) >= 2, str(miss))
    check("① 模糊说法被抓", any(m.startswith("VAGUE:") for m in miss), str([m for m in miss if m.startswith("VAGUE")]))
    check("① 抽 token 含百分号", "20%" in numeric_tokens(src), str(numeric_tokens(src)))

    # ② 物料原文直出
    mat = json.loads((PROJ / "materials/20261120_aion.json").read_text(encoding="utf-8"))
    desc = material_body_desc(mat)
    check("② 物料 body 原样返回", desc == mat["body"].strip(), f"{len(desc)} 字")
    long_body = "第一行" * 40 + "\n" + "第二行" * 200
    cut = material_body_desc({"body": long_body}, limit=200)
    check("② 长文按整行截断且不砍半句",
          len(cut) <= 200 and long_body.startswith(cut) and not cut.endswith("第"),
          f"{len(cut)} 字 / {cut.count(chr(10))} 换行")

    # ③ 代码里不再有 LLM 复写正文的路径
    vd = read("src/utils/video_desc.py")
    ensure = vd.split("def ensure_desc")[1].split("def _tags_str")[0]
    check("③ ensure_desc 不再调 generate_desc", "generate_desc(" not in ensure)
    check("③ ensure_desc 不再 humanize(video_desc)", "humanize(vd)" not in ensure)
    check("③ build_desc 有数字保真闸门", "check_desc_fidelity" in vd and "DescFidelityError" in vd)

    he = read("src/hotspot_extractor.py")
    check("③ 生成阶段用物料 body 覆盖 video_desc",
          "VIDEO_DESC_OVERRIDDEN" in he and "material_body_desc" in he)

    # ④ 视频号：不拼 title、回读不一致要拦
    ch = read("src/providers/channels_cdp.py")
    check("④ 正文不再前置 title", 'f"{title}\\n\\n' not in ch)
    check("④ 360 字硬截断已去掉", "_cap_lines(content_text)" not in ch)
    check("④ 回读不一致 → DESC_MISMATCH 阻断", "DESC_MISMATCH" in ch)

    # ⑤ 话题芯片文字校验
    df = read("src/providers/douyin_form.py")
    check("⑤ 话题芯片文字 != 期望要记账", "芯片文字被平台改写" in df)
    check("⑤ 无联想项不写纯文本（旧约束仍在）", "无匹配联想项" in df)

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
