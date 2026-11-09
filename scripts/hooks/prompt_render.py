#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.prompt_render（transform）—— 提示词组装。

把 material_block + 目标账号 + 结构要求拼成 llm.main 的最终 prompt，并回传 fingerprint，
便于事后复现"这条片子是哪版提示词生成的"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

TEMPLATE = """你是一名资深科技短视频架构师。请把下面的物料扩写为可渲染的 DraftScriptPayload JSON。

{account_line}
{material_block}

【输出硬约束】
1. 只能基于 <material> 中的事实扩写，严禁引入物料里不存在的数字、结论、公司或产品名；
2. 物料未提供的信息一律留空或省略，不得编造；
3. 输出必须是单个 JSON object，字段遵循 DraftScriptPayload 结构；
4. 正文口播控制在 700-900 字，分镜与口播一一对应。
"""


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    block = c.get("material_block")
    if not block:
        C.fail("prompt_render: 缺 material_block（请先跑 hook.material_bundle）")

    account = c.get("account") or "unknown"
    account_line = f"【目标账号】{account}（按其平台风格调整长度与语气）"
    prompt = TEMPLATE.format(account_line=account_line, material_block=block)

    C.emit(True, patch={
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "prompt_fp": C.sha256_text(prompt)[:16],
    })


if __name__ == "__main__":
    main()
