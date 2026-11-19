#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水墨模板（ink_story）契约测试 —— 2026-11-19 SPEC 方案 C。

判据：
  T1  素材库已注册：config/assets.yaml 有 ink_story，root 指向 renderer/public/ink
  T2  manifest 可读且 6 槽位声明齐全（open/reveal/bg/mid/end/tex）
  T3  bg 槽位必须有素材（否则整库不可用于出片）
  T4  Selection 带 reveal / tex 字段，to_render_props 会注入这两个键
  T5  模板映射：ink_story → InkStoryVideo；textbook_story → TextbookStoryVideo
  T6  渲染层真的用了背景层：InkStoryVideo 与 TextbookVideo 都 import InkBackdropLayer
  T7  ink 素材全部竖版 9:16（用 ffprobe 实测，不信任文件名）

任何一条不过 → 退出码 1，禁止出片。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parents[1]
PUBLIC = PROJ / "renderer" / "public"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ← {detail}" if detail and not ok else ""))


def main() -> int:
    cfg = yaml.safe_load((PROJ / "config" / "assets.yaml").read_text(encoding="utf-8"))
    libs = cfg.get("libraries") or {}
    ink = libs.get("ink_story") or {}
    check("T1 素材库 ink_story 已注册", bool(ink), f"现有库: {sorted(libs)}")
    check("T1 root 指向 renderer/public/ink",
          str(ink.get("root", "")).replace("\\", "/") == "renderer/public/ink", str(ink.get("root")))

    man_path = PROJ / str(ink.get("manifest") or "")
    man = yaml.safe_load(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
    assets = (man or {}).get("assets") or []
    slots_cfg = (ink.get("slots") or {})
    declared = {"open", "reveal", "bg", "mid", "end", "tex"}
    check("T2 manifest 存在且可读", man_path.exists(), str(man_path))
    check("T2 6 槽位全部在 assets.yaml 声明", declared <= set(slots_cfg),
          f"缺: {sorted(declared - set(slots_cfg))}")
    check("T2 每条素材必填 id/file/slot/license",
          all({"id", "file", "slot", "license"} <= set(a) for a in assets),
          json.dumps(assets[:2], ensure_ascii=False))

    bg_assets = [a for a in assets if a.get("slot") == "bg"]
    check("T3 bg 槽位有素材（required=true）", bool(bg_assets), f"bg 条数={len(bg_assets)}")

    sys.path.insert(0, str(PROJ))
    from src.assets_manager import AssetManager, Selection  # noqa: E402

    fields = set(Selection.__dataclass_fields__)
    check("T4 Selection 带 reveal/tex 字段", {"reveal", "tex"} <= fields,
          f"缺: {sorted({'reveal','tex'} - fields)}")

    try:
        sel = AssetManager().select(run_id="ink-contract-check", library="ink_story")
        props = sel.to_render_props()
        check("T4 select(library=ink_story) 可跑通", True)
        check("T4 to_render_props 注入 reveal/tex 键", {"reveal", "tex"} <= set(props.get("assets") or {}),
              json.dumps(sorted((props.get("assets") or {})), ensure_ascii=False))
        check("T4 选中素材路径以 ink/ 开头", str(props["assets"]["bg"]).startswith("ink/"),
              str(props["assets"]["bg"]))
    except Exception as exc:  # noqa: BLE001
        check("T4 select(library=ink_story) 可跑通", False, f"{type(exc).__name__}: {exc}")
        check("T4 to_render_props 注入 reveal/tex 键", False, "上一步失败")
        check("T4 选中素材路径以 ink/ 开头", False, "上一步失败")

    from src.pipeline.executors import COMPOSITION_BY_TEMPLATE  # noqa: E402

    check("T5 ink_story → InkStoryVideo",
          COMPOSITION_BY_TEMPLATE.get("ink_story") == "InkStoryVideo",
          str(COMPOSITION_BY_TEMPLATE.get("ink_story")))
    check("T5 textbook_story → TextbookStoryVideo",
          COMPOSITION_BY_TEMPLATE.get("textbook_story") == "TextbookStoryVideo",
          str(COMPOSITION_BY_TEMPLATE.get("textbook_story")))

    ink_tsx = (PROJ / "renderer" / "src" / "ink" / "InkStoryVideo.tsx").read_text(encoding="utf-8")
    tb_tsx = (PROJ / "renderer" / "src" / "textbook" / "TextbookVideo.tsx").read_text(encoding="utf-8")
    check("T6 InkStoryVideo 挂了水墨背景层", "InkBackdropLayer" in ink_tsx)
    check("T6 TextbookVideo 挂了水墨背景层", "InkBackdropLayer" in tb_tsx)

    bad = []
    for a in assets:
        f = PUBLIC / "ink" / str(a.get("file"))
        if not f.exists():
            bad.append(f"{a.get('id')}: 文件不存在")
            continue
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(f)],
            capture_output=True, text=True).stdout.strip()
        try:
            w, h = (int(x) for x in out.split(",")[:2])
        except Exception:  # noqa: BLE001
            bad.append(f"{a.get('id')}: ffprobe 读不出尺寸")
            continue
        if abs(w / h - 9 / 16) > 0.01:
            bad.append(f"{a.get('id')}: {w}×{h} 不是 9:16")
    check("T7 ink 素材全部竖版 9:16", not bad, "; ".join(bad))

    print(f"\n汇总: {len(PASS)}/{len(PASS) + len(FAIL)} PASS")
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
