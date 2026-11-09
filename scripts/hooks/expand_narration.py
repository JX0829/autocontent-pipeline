#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.expand_narration（transform）—— 分镜/口播/字幕切分。

从 draft 的正文切出可配音的分句序列（按中文句末标点切，合并过短句），
并给出预计时长（4.5 字/秒，Edge-TTS +10% 语速的经验值）。
渲染层只消费这里产出的 narration，不再各自切分。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

CHARS_PER_SEC = 4.5
MIN_SEG = 8


def _split(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    segs, buf = [], ""
    for raw in parts:
        s = raw.strip()
        if not s:
            continue
        buf += s
        if len(buf) >= MIN_SEG:
            segs.append(buf)
            buf = ""
    if buf:
        segs.append(buf)
    return segs


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script"))
    if not isinstance(draft, dict):
        C.fail("expand_narration: 缺 draft_script（context.draft / artifacts.draft_script）")

    body = str(draft.get("body") or draft.get("narration") or draft.get("content") or "")
    if len(body) < 20:
        C.fail(f"expand_narration: 正文过短（{len(body)} 字），无法切分")

    segs = _split(body)
    if not segs:
        C.fail("expand_narration: 切分后为空")

    total = sum(len(s) for s in segs)
    C.emit(True, patch={
        "narration": segs,
        "narration_count": len(segs),
        "narration_chars": total,
        "expected_duration_sec": round(total / CHARS_PER_SEC, 1),
    })


if __name__ == "__main__":
    main()
