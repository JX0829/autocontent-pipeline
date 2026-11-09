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
