# -*- coding: utf-8 -*-
"""出厂文本的**唯一**敏感表述替换表（2026-11-12）。

问题背景（「黄仁勋辞任」6 次出片中的缺陷①）：
    `src/tts/aligner.py` 在旁白里把「推上/推特/twitter」替换成「技术圈/全网技术圈」，
    但 `scripts/hooks/sensitive_scan.py` 的门禁用的是 `config/sensitive_words.txt`，
    同一批词在词表里是**直接 block** —— 于是流水线自己会改写的东西，先被自己的门禁毙了，
    表现就是「敏感词『推上』误杀 block（与 aligner 替换策略不一致）」。

现在只维护这一张表：
    · `sensitive_scan` 用它先做一遍「门禁预览」：能自动改写的词不算违规，并回报改写项；
    · `aligner` 用它做真正的旁白改写（此前是两份各自维护的列表，必然漂移）。
"""
from __future__ import annotations

import re

#: (正则, 替换文本)。顺序有意义：先长词后短词，避免「推特」先被部分替换。
REWRITES: list[tuple[str, str]] = [
    (r"(?i)twitter", "前沿开源社区"),
    (r"推特", "全网技术圈"),
    (r"推上", "技术圈"),
    (r"推友", "全球开发者"),
    (r"X平台", "海外开源社区"),
    (r"x\.com", "开源主页"),
    (r"外网", "全球技术社区"),
    (r"外媒报道", "最新技术资讯"),
    (r"无审查", "性能极限优化"),
    (r"(?i)uncensored", "开源完全可控"),
]


def apply_rewrites(text: str) -> tuple[str, list[str]]:
    """返回 (改写后的文本, 命中的改写说明)。说明形如 `推上→技术圈`，便于门禁如实回报。"""
    if not text:
        return text, []
    out = text
    applied: list[str] = []
    for pattern, target in REWRITES:
        new = re.sub(pattern, target, out)
        if new != out:
            applied.append(f"{pattern}→{target}")
            out = new
    return out, applied


def rewritable_words() -> list[str]:
    """替换表覆盖的字面词（供测试/文档用；正则里的转义不做还原）。"""
    return [p for p, _ in REWRITES]
