#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""上传封面统一口径：需要封面的平台，用成片前 0.5s 内的一帧。

用户 2026-11-20 口径原文：「每个平台的规则不一样，如果需要上传封面，
那就把 0.5 秒以内的一帧作为封面，因为前 0.5 秒都是封面帧。」

各平台差异（当前实现）：
  · 抖音    —— 默认不传封面，平台自抽首帧；首帧 == cover.png 已由片头保证
  · 视频号  —— Web 端没有封面控件
  · B 站    —— **必须有封面**，走本模块

优先级（越高越优先）：
  1. 调用方显式给的文件（存在且非空）
  2. 成片同目录 `cover_upload_16_9.png` / `cover_upload.png`（pipeline 已抽好）
  3. 同目录 `cover_16_9.png` / `cover.png`（封面源图；与成片首帧同图）
  4. 现场从成片 0.25s 抽帧 + 静止性自证（0/0.25/0.45 三帧一致）

前 0.5s 不是静止封面帧 → 返回 ""（调用方必须 fail-closed，不能拿正片画面当封面）。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("upload_cover")

PORTRAIT_CANDIDATES = ("cover_upload.png", "cover.png",
                       "cover_upload_16_9.png", "cover_16_9.png")
LANDSCAPE_CANDIDATES = ("cover_upload_16_9.png", "cover_16_9.png",
                        "cover_upload.png", "cover.png")
CANDIDATES = LANDSCAPE_CANDIDATES   # 兼容旧引用


def _is_landscape(video: Path) -> bool:
    """按成片真实尺寸判断画幅（拿不到就当竖版）。"""
    import subprocess
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0:s=x", str(video)],
                           capture_output=True, text=True, timeout=20)
        w, h = (int(x) for x in p.stdout.strip().split("x")[:2])
        return w > h
    except Exception:  # noqa: BLE001
        return False


def ensure_upload_cover(video: str | Path, given: str | Path | None = None,
                        *, allow_extract: bool = True) -> tuple[str, str]:
    """返回 (封面路径, 依据说明)；拿不到合规封面时返回 ("", 原因)。"""
    video = Path(video)
    if given:
        g = Path(given)
        if g.exists() and g.stat().st_size > 0:
            return str(g), f"显式传入:{g.name}"

    cands = LANDSCAPE_CANDIDATES if _is_landscape(video) else PORTRAIT_CANDIDATES
    for name in cands:
        cand = video.with_name(name)
        if cand.exists() and cand.stat().st_size > 0:
            return str(cand), f"同目录:{cand.name}（前 0.5s 封面帧/封面源图）"

    if not allow_extract:
        return "", "没有可用封面文件（未允许现场抽帧）"
    if not video.exists():
        return "", f"成片不存在:{video}"

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from src.pipeline.cover_lead import CoverLeadError, make_upload_cover
    except Exception as exc:  # noqa: BLE001
        logger.warning("封面抽帧模块不可用：%s: %s", type(exc).__name__, exc)
        return "", f"抽帧模块不可用:{type(exc).__name__}: {exc}"

    out = video.with_name("upload_cover_from_lead.jpg")
    try:
        info = make_upload_cover(video, out, cover_ref=None)
    except CoverLeadError as exc:
        logger.warning("前 0.5s 不是静止封面帧，拒绝当封面：%s", exc)
        return "", f"前 0.5s 不是静止封面帧:{exc}"
    return str(info["path"]), f"现场抽帧@{info['at_sec']}s（spread={info['static_spread']}）"


__all__ = ["ensure_upload_cover", "CANDIDATES"]
