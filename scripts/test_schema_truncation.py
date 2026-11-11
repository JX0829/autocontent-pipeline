#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约测试：fact_summary_150 截断。

锁住 803b583 的修复，防止以后有人把 validator 删了/改坏，
让 LLM 超长输出重新把整条草稿崩在 string_too_long。

跑法：python3 scripts/test_schema_truncation.py
退出码：0 全过；1 有失败
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from pydantic import ValidationError  # noqa: E402

from src.schemas import DraftScriptPayload  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}" + (f"（{extra}）" if extra else ""))
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f"（{extra}）" if extra else ""))


BASE: dict[str, Any] = {
    "theme": "tech-github",
    "entity_key": "k",
    "title": "测试标题",
    "cover_main_text": "主标题",
    "cover_sub_text": "副标题",
    "hook_narration": "开场钩子",
    "resource_keyword": "astryx",
    "template_id": "auto",
    "tags": [],
    "target_account_type": "tech",
    "scenes": [
        {"scene_index": i, "segment_narration": "旁白", "visual_type": "terminal_code",
         "highlight_text": "高亮", "sfx_cue": "none"}
        for i in range(3)
    ],
}


def build(summary: object) -> DraftScriptPayload:
    return DraftScriptPayload.model_validate({**BASE, "fact_summary_150": summary})


def main() -> int:
    print("【fact_summary_150 截断契约】")

    short = "Meta 开源内部八年设计系统 Astryx。"
    m = build(short)
    check("短文本原样保留", m.fact_summary_150 == short, f"{len(m.fact_summary_150)} 字")

    # 真实现场值：跑 astryx 物料时 LLM 输出 158 字，直接触发 string_too_long
    real_158 = (
        "Meta 把内部用了八年的设计系统 Astryx 全量开源，MIT 协议，代码与设计文档一并公开。"
        "它是一套面向大型产品团队的组件与设计令牌体系，覆盖表格、表单、导航等高频场景，"
        "强调无障碍与主题化，官方给出与 Figma 变量同源的令牌导出工具，"
        "基于 React 19 与 StyleX 构建，目前处于 Beta 阶段。"
    )
    check("现场真值长度对齐（基准样本）", len(real_158) > 150, f"{len(real_158)} 字")
    m = build(real_158)
    check("超长按标点截断到 150 以内", len(m.fact_summary_150) <= 150,
          f"{len(real_158)} → {len(m.fact_summary_150)} 字")
    check("截断后以句末标点收尾",
          m.fact_summary_150.endswith(("。", "！", "？", "…")),
          repr(m.fact_summary_150[-14:]))
    check("截断后不为空", len(m.fact_summary_150) >= 100, f"{len(m.fact_summary_150)} 字")

    # 远超上限（真实日志里出现过 250+）
    huge = real_158 * 2
    m = build(huge)
    check("超长两倍仍收敛到 150 以内", len(m.fact_summary_150) <= 150, f"{len(huge)} 字")

    # 中段无标点：只能硬切，但不能崩
    no_punct = "开" * 400
    m = build(no_punct)
    check("无标点长文本硬切不报错", len(m.fact_summary_150) <= 150,
          f"{len(m.fact_summary_150)} 字")

    # 幂等：截断结果再进一次不应该继续变短
    m2 = build(m.fact_summary_150)
    check("截断结果幂等（二次校验不变）",
          len(m2.fact_summary_150) == len(m.fact_summary_150),
          f"{len(m.fact_summary_150)} vs {len(m2.fact_summary_150)}")

    # 边界：正好 150 字不动
    exact = "字" * 150
    m = build(exact)
    check("正好 150 字不截断", m.fact_summary_150 == exact, "150 字")

    # 兜底：max_length=200 仍然存在（截断逻辑失效时还能拦）
    field = DraftScriptPayload.model_fields["fact_summary_150"]
    limits = [getattr(md, "max_length", None) for md in field.metadata]
    check("字段仍保留 max_length=200 兜底", 200 in limits, str(limits))

    # 反向：如果 validator 被摘掉，超长必须报错（证明这个测试真的在守门）
    class _NoSanitize(DraftScriptPayload):
        @classmethod
        def sanitize_fact_summary(cls, v):  # noqa: ANN001
            return v

    try:
        _NoSanitize.model_validate({**BASE, "fact_summary_150": huge})
        check("反向验证：未经截断的超长应被 200 上限拦下", False, "居然通过了")
    except ValidationError as exc:
        types = [e["type"] for e in exc.errors()]
        check("反向验证：未经截断的超长被 string_too_long 拦下",
              "string_too_long" in types, str(types))

    print(f"\n{'✅ PASS' if FAIL == 0 else '❌ FAIL'} {PASS}   ❌ FAIL {FAIL}")
    print("SCHEMA TRUNCATION PASS" if FAIL == 0 else "SCHEMA TRUNCATION FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
