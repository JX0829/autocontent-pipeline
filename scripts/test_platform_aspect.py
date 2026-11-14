#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站画幅回归：发布方向必须"按平台"而不是"全局写死"（2026-11-14 返工）

事故：`pipeline.yaml::publish.aspect: vertical` 被 executors 当成 forced_aspect，
短路了 `PLATFORM_ASPECT[bilibili]="landscape"`，B站连发三条 1080x1920 竖屏。
本用例钉住三件事：
  1. 配置里不得再出现强制的 publish.aspect（留空 = 按平台）
  2. 三平台的映射值正确（bilibili=landscape，其余 vertical）
  3. 横版链路还在（render_landscape 产出 video_16_9/cover_16_9 + deliver 两版都发）
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import yaml  # noqa: E402

from src.pipeline.contracts import load_pipeline  # noqa: E402
from src.utils.accounts import PLATFORM_ASPECT, aspect_for_platform  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


class _FakeCtx:
    """_pick_media 只用到 artifacts / run_root。"""

    def __init__(self, run_root: Path, artifacts: dict[str, str] | None = None):
        self.run_root = run_root
        self.artifacts = artifacts or {}


def test_config_has_no_forced_aspect() -> None:
    cfg = load_pipeline() or {}
    pub = cfg.get("publish") or {}
    forced = str(pub.get("aspect") or "").strip()
    check("① publish.aspect 不得写死（留空=按平台）", forced == "", f"实际={forced or '(空)'}")


def test_platform_mapping() -> None:
    check("② bilibili → landscape", aspect_for_platform("bilibili") == "landscape",
          aspect_for_platform("bilibili"))
    for p in ("douyin", "channels", "weixin"):
        check(f"② {p} → vertical", aspect_for_platform(p) == "vertical", aspect_for_platform(p))
    check("② 未知平台回落 vertical", aspect_for_platform("unknown") == "vertical")
    check("② 映射表本身含 bilibili", PLATFORM_ASPECT.get("bilibili") == "landscape")


def test_pick_media_per_platform() -> None:
    from src.pipeline.executors import _pick_media

    root = PROJ / "output" / "20260101" / "demo"
    for plat in ("bilibili", "douyin", "channels"):
        forced = ""                                   # pipeline.yaml 现在的取值
        aspect = forced or aspect_for_platform(plat)
        video, cover = _pick_media(_FakeCtx(root), aspect)
        want = ("video_16_9.mp4", "cover_16_9.png") if plat == "bilibili" else ("video.mp4", "cover.png")
        check(f"③ {plat} 选片 = {want[0]}", (video.name, cover.name) == want,
              f"{video.name}/{cover.name}")

    # 负向：手工单平台显式强制仍必须生效（返工用的逃生口）
    aspect = "vertical" or aspect_for_platform("bilibili")
    video, _ = _pick_media(_FakeCtx(root), aspect)
    check("③ 显式强制 vertical 仍覆盖平台映射", video.name == "video.mp4", video.name)


def test_landscape_chain_intact() -> None:
    cfg = load_pipeline() or {}
    steps = {s["id"]: s for s in cfg.get("steps") or []}
    ls = steps.get("render_landscape")
    check("④ render_landscape 步仍在", ls is not None)
    if ls:
        check("④ 横版产物含 video_16_9 + cover_16_9",
              set(ls.get("produces") or []) >= {"video_16_9.mp4", "cover_16_9.png"},
              str(ls.get("produces")))
        check("④ 横版尺寸 1920x1080",
              (ls.get("params") or {}).get("width") == 1920
              and (ls.get("params") or {}).get("height") == 1080,
              str(ls.get("params")))
    dv = steps.get("deliver")
    if dv:
        check("④ deliver 仍是两版都发（审核要看横竖）",
              set((dv.get("params") or {}).get("aspects") or []) == {"vertical", "landscape"},
              str((dv.get("params") or {}).get("aspects")))
        check("④ deliver reads 两版成片",
              set(dv.get("reads") or []) == {"video.mp4", "video_16_9.mp4"},
              str(dv.get("reads")))
    aspects = (cfg.get("inputs") or {}).get("aspects", {})
    check("④ inputs.aspects 仍含 landscape",
          "landscape" in (aspects.get("default") or []), str(aspects.get("default")))


def test_bilibili_uploader_gets_landscape() -> None:
    """B站 CLI 默认封面必须是 cover_16_9.png（横版），否则会无封面发布。"""
    src = (PROJ / "scripts" / "bilibili_live_uploader.py").read_text(encoding="utf-8")
    check("⑤ B站 uploader 默认图封面 = cover_16_9.png", 'cover_16_9.png' in src)


def main() -> int:
    test_config_has_no_forced_aspect()
    test_platform_mapping()
    test_pick_media_per_platform()
    test_landscape_chain_intact()
    test_bilibili_uploader_gets_landscape()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
