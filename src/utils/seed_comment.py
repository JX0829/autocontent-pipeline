#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作者首评（seed comment）统一解析 —— 2026-11-10 补齐的漏字段。

背景：三个平台的「发布后自动发作者首评并尽量置顶」原先散落在各 provider 里，
B站有、抖音/视频号没有，且没有任何开关。现在统一由 `config/accounts.yaml` 的
`seed_comment` 块驱动：

    seed_comment:
      enabled: true      # 发布成功后自动发首评
      pin: true          # 能置顶就置顶（首屏默认可见）
      text: ""           # 留空则用 video_desc 前 120 字 + 欢迎语

平台能力差异（capability）：
- bilibili：支持「发首评 + 置顶」，接口偶发 -404（稿件刚落库），需重试
- douyin  ：支持作者评论与置顶，但只能走 CDP 真机 UI
- channels：视频号 Web 端无作者首评接口，需手机端手动（默认跳过并显式告警）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger("SeedComment")

SUPPORTED = {"bilibili": True, "douyin": True, "channels": False}
PIN_SUPPORTED = {"bilibili": True, "douyin": True, "channels": False}
DEFAULT_TAIL = "欢迎在评论区交流讨论～"


def resolve(account_cfg: Dict[str, Any], draft: Dict[str, Any] | None = None,
            platform: str = "") -> Tuple[bool, bool, str]:
    """返回 (是否发首评, 是否置顶, 评论文本)。"""
    cfg = (account_cfg or {}).get("seed_comment") or {}
    enabled = bool(cfg.get("enabled", False))
    pin = bool(cfg.get("pin", True))
    if not enabled:
        return False, False, ""

    text = str(cfg.get("text") or "").strip()
    if not text:
        draft = draft or {}
        body = str(draft.get("video_desc") or draft.get("fact_summary_150") or "").strip()
        text = f"{body[:120]}\n\n{DEFAULT_TAIL}".strip() if body else DEFAULT_TAIL

    plat = platform or str((account_cfg or {}).get("platform") or "")
    if plat and not SUPPORTED.get(plat, False):
        logger.warning(f"SEED_COMMENT_UNSUPPORTED: platform={plat} 无 Web 作者首评能力，跳过（需手机端手动）")
        return False, False, text
    if not PIN_SUPPORTED.get(plat, False):
        pin = False
    return True, pin, text
