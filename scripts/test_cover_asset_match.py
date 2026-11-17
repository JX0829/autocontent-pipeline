#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面语义选材回归（SPEC-20261117 R2/R3/R5）

触发事故：`huang-newco-1`（人物/公司治理选题）首帧用的是晶圆特写，
文案讲人事、画面讲制造工艺 —— 因为素材池只有 tech_process 一档且选材无语义层。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def _mgr():
    from src.assets_manager import AssetManager
    # 每个用例用独立的 history 文件，避免「最近用过」影响可复现性
    tmp = pathlib.Path(tempfile.mkdtemp()) / "hist.json"
    return AssetManager(history_path=tmp)


def test_r5_no_matching_asset() -> None:
    from src.assets_manager import NO_MATCHING_ASSET_FALLBACK
    d = {"title": "传黄仁勋另立公司，实为求回归",
         "tags": ["黄仁勋", "英伟达", "公司治理"],
         "scenes": [{"segment_narration": "董事会请律所调查他的回归传闻"}]}
    sel = _mgr().select(run_id="t-r5-1", draft=d)
    check("R5 人物/公司选题判为 people_event", sel.domain == "people_event", str(sel.domain))
    check("R5 无匹配档 → 封面回退纯排版", sel.cover_mode == "typographic", sel.cover_mode)
    check("R5 不静默复用晶圆做开场", sel.open is None, str(sel.open and sel.open.id))
    check("R5 打 NO_MATCHING_ASSET_FALLBACK",
          any(NO_MATCHING_ASSET_FALLBACK in w for w in sel.warnings), str(sel.warnings))
    props = sel.to_render_props()["assets"]
    check("R5 render props 带 cover_mode", props.get("cover_mode") == "typographic", json.dumps(props, ensure_ascii=False))


def test_r2_tech_still_uses_assets() -> None:
    d = {"title": "ggml 新后端把显存压到一半", "tags": ["开源", "推理", "显存"],
         "scenes": [{"segment_narration": "这个推理框架用 GPU 算子重写了 kernel"}]}
    sel = _mgr().select(run_id="t-r2-1", draft=d)
    check("R2 技术选题判为 tech_process", sel.domain == "tech_process", str(sel.domain))
    check("R2 技术选题仍用素材帧封面", sel.cover_mode == "asset", sel.cover_mode)
    check("R2 技术选题有开场素材", sel.open is not None and "open" in sel.open.src, str(sel.open and sel.open.src))


def test_r2_unknown_signal() -> None:
    from src.assets_manager import NO_DOMAIN_SIGNAL
    sel = _mgr().select(run_id="t-r2-2", draft={"title": "今天聊聊这个", "tags": [], "scenes": []})
    check("R2 判不出档位 → 也回退纯排版（R5 口径）", sel.cover_mode == "typographic", sel.cover_mode)
    check("R2 打 NO_DOMAIN_SIGNAL", any(NO_DOMAIN_SIGNAL in w for w in sel.warnings), str(sel.warnings))


def test_r3_material_lock() -> None:
    from src.assets_manager import AssetError
    d = {"title": "ggml 新后端把显存压到一半", "tags": ["推理"], "scenes": []}
    sel = _mgr().select(run_id="t-r3-1", draft=d, locked={"open": "open_wafer_v1"})
    check("R3 物料锁定素材被尊重", sel.open is not None and sel.open.id == "open_wafer_v1",
          str(sel.open and sel.open.id))
    check("R3 锁定留痕可查", any("按物料锁定选用" in w for w in sel.warnings), str(sel.warnings))
    check("R3 锁定不影响其它位", sel.cover_mode in ("asset", "typographic"), sel.cover_mode)

    try:
        _mgr().select(run_id="t-r3-2", draft=d, locked={"open": "not_exist_v9"})
        check("R3 锁定不存在的素材 → 报错不静默换", False, "没有抛 AssetError")
    except AssetError as exc:
        check("R3 锁定不存在的素材 → 报错不静默换", "not_exist_v9" in str(exc), str(exc)[:60])


def test_signals_are_readonly() -> None:
    src = (PROJ / "src" / "assets_manager.py").read_text(encoding="utf-8")
    check("R2 语义只读产物（不调 LLM）", "LLMClient" not in src and "openai" not in src)
    ex = (PROJ / "src" / "pipeline" / "executors.py").read_text(encoding="utf-8")
    check("R3 物料字段接进选材", '"cover_asset"' in ex and '"open_asset"' in ex)
    tsx = (PROJ / "renderer" / "src" / "tech" / "TechStoryVideo.tsx").read_text(encoding="utf-8")
    check("R5 渲染器识别 typographic 封面", "coverMode === 'typographic'" in tsx)


def main() -> int:
    test_r5_no_matching_asset()
    test_r2_tech_still_uses_assets()
    test_r2_unknown_signal()
    test_r3_material_lock()
    test_signals_are_readonly()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
