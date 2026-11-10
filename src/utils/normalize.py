#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T2.4 文本归一化 —— **全仓唯一实现**（spec §5 T2.4）

评论判重、选题去重、内容指纹三处必须用同一套归一化，否则「同一内容不同排版」
会在不同链路上得到不同结论（QA #11 判重精确匹配失效的根因）。

规则：
  1. 剥离 `[捂脸R]` 这类表情码（1~8 字符，可带 R 结尾）
  2. 小写
  3. 只保留字母数字与中日韩汉字，去掉所有空白与标点
"""
from __future__ import annotations

import re

_MARKUP_RE = re.compile(r"\[[^\]]{1,8}R?\]")
_NON_WORD_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def normalize_text(s: str) -> str:
    """归一化文本用于指纹/去重比对（幂等：n(n(x)) == n(x)）。"""
    s = _MARKUP_RE.sub("", s or "")
    s = s.lower()
    return _NON_WORD_RE.sub("", s)


def content_hash(title: str, body: str, slug: str = "") -> str:
    """内容指纹：sha256(normalize(title + slug + body))，返回完整 hex（调用方自行截断）。"""
    import hashlib
    return hashlib.sha256(normalize_text(f"{title}{slug}{body}").encode("utf-8")).hexdigest()


# ---------------- T3.1：entity_key 归一化（QA R1）----------------
#
# 病根：模型给出的 entity_key 形如 `hackernews:claude-code-...-1794114373`，
# 尾部是 unix 时间戳 → 同一事件重跑就生成新 key，`UNIQUE(entity_key)` 永不触发。
# 修法：写入/比对前把尾部时间戳剥掉，比较口径统一。

_TS_SUFFIX_RE = re.compile(r"-(\d{10})$")
_TS_RANGE = (1_000_000_000, 2_200_000_000)   # 2001-09 ~ 2039-09，只剥这个区间的 10 位数


def normalize_entity_key(key: str) -> str:
    """剥掉 entity_key 尾部的 unix 时间戳（幂等）。

    `hackernews:claude-code-leak-1794114373` → `hackernews:claude-code-leak`

    只剥 10 位且在合理 unix 时间区间内的后缀，避免误伤 `gpt-4-0613` 这类版本号。

    **不改大小写**：`github:Owner/Repo` 是历史数据里的原始标识，存储层保持原样
    （实测若在此小写化，会额外改动 17 行无关历史数据）；比较时由调用方 `.lower()`。
    """
    s = (key or "").strip()
    m = _TS_SUFFIX_RE.search(s)
    if m:
        ts = int(m.group(1))
        if _TS_RANGE[0] <= ts <= _TS_RANGE[1]:
            s = s[: m.start()]
    return s.strip()
