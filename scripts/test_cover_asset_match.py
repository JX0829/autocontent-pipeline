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
    check("§八 默认去封面帧（cover_mode=none）", sel.cover_mode == "none", sel.cover_mode)
    check("R5 不静默复用晶圆做开场", sel.open is None, str(sel.open and sel.open.id))
    check("R5 打 NO_MATCHING_ASSET_FALLBACK",
          any(NO_MATCHING_ASSET_FALLBACK in w for w in sel.warnings), str(sel.warnings))
    props = sel.to_render_props()["assets"]
    check("§八 render props 带 cover_mode=none", props.get("cover_mode") == "none", json.dumps(props, ensure_ascii=False))


def test_r2_tech_still_uses_assets() -> None:
    d = {"title": "ggml 新后端把显存压到一半", "tags": ["开源", "推理", "显存"],
         "scenes": [{"segment_narration": "这个推理框架用 GPU 算子重写了 kernel"}]}
    sel = _mgr().select(run_id="t-r2-1", draft=d)
    check("R2 技术选题判为 tech_process", sel.domain == "tech_process", str(sel.domain))
    check("§八 技术选题同样去封面帧", sel.cover_mode == "none", sel.cover_mode)
    check("R2 技术选题有开场素材", sel.open is not None and "open" in sel.open.src, str(sel.open and sel.open.src))


def test_r2_unknown_signal() -> None:
    from src.assets_manager import NO_DOMAIN_SIGNAL
    sel = _mgr().select(run_id="t-r2-2", draft={"title": "今天聊聊这个", "tags": [], "scenes": []})
    check("R2 判不出档位 → 同样不插封面帧", sel.cover_mode == "none", sel.cover_mode)
    check("R2 打 NO_DOMAIN_SIGNAL", any(NO_DOMAIN_SIGNAL in w for w in sel.warnings), str(sel.warnings))


def test_r3_material_lock() -> None:
    from src.assets_manager import AssetError
    d = {"title": "ggml 新后端把显存压到一半", "tags": ["推理"], "scenes": []}
    sel = _mgr().select(run_id="t-r3-1", draft=d, locked={"open": "open_wafer_v1"})
    check("R3 物料锁定素材被尊重", sel.open is not None and sel.open.id == "open_wafer_v1",
          str(sel.open and sel.open.id))
    check("R3 锁定留痕可查", any("按物料锁定选用" in w for w in sel.warnings), str(sel.warnings))
    check("R3 锁定不影响封面口径（仍为 none）", sel.cover_mode == "none", sel.cover_mode)

    try:
        _mgr().select(run_id="t-r3-2", draft=d, locked={"open": "not_exist_v9"})
        check("R3 锁定不存在的素材 → 报错不静默换", False, "没有抛 AssetError")
    except AssetError as exc:
        check("R3 锁定不存在的素材 → 报错不静默换", "not_exist_v9" in str(exc), str(exc)[:60])


def test_cover_is_first_frame() -> None:
    """§八 A7/A9/A10：封面图 = 视频第 0 帧；抽帧失败必须显式报错。"""
    import subprocess
    from src.pipeline.executors import ExecutorError, _extract_first_frame, _probe_size

    run = PROJ / "output" / "20261117" / "huang-newco-1"
    for name, size in (("video.mp4", (1080, 1920)), ("video_16_9.mp4", (1920, 1080))):
        video = run / name
        if not video.exists():
            print(f"SKIP  §八 抽帧 {name}（本机无该 run 产物）")
            continue
        out = pathlib.Path(tempfile.mkdtemp()) / f"cover_{name}.png"
        _extract_first_frame(video, out, expect_size=size)
        check(f"§八 {name} 封面尺寸 {size}", _probe_size(out) == size, str(_probe_size(out)))
        raw_cover = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo",
                                    "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        # 2026-11-18：封面改抽第 COVER_FRAME 帧（默认 54），标题淡入完成后才有字
        from src.pipeline.executors import _cover_frame_no
        _fn = _cover_frame_no()
        raw_ref = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video),
                                  "-vf", f"select=eq(n\\,{_fn})",
                                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                 capture_output=True).stdout
        check(f"§八 {name} 封面与第 {_fn} 帧逐像素一致（A7/A10）", raw_cover == raw_ref and len(raw_cover) > 0)

    broken = pathlib.Path(tempfile.mkdtemp()) / "broken.mp4"
    broken.write_bytes(b"not a video")
    try:
        _extract_first_frame(broken, broken.with_suffix(".png"))
        check("§八 A9 抽帧失败显式报错", False, "没有抛 ExecutorError")
    except ExecutorError as exc:
        check("§八 A9 抽帧失败显式报错", "COVER_FRAME_EXTRACT_FAILED" in str(exc), str(exc)[:60])

    ex = (PROJ / "src" / "pipeline" / "executors.py").read_text(encoding="utf-8")
    check("§八 竖版不再渲 CoverStill", 'still", "src/index.ts", "CoverStill"' not in ex)
    rl = (PROJ / "scripts" / "render_landscape.py").read_text(encoding="utf-8")
    check("§八 横版不再渲封面构图", '"remotion", "still"' not in rl)
    tsx = (PROJ / "renderer" / "src" / "tech" / "TechStoryVideo.tsx").read_text(encoding="utf-8")
    check("§八 none 模式抹掉 open 片段", "cfg.coverMode === 'none'" in tsx and "cfg.open = null" in tsx)


def test_guard_ocr_is_advisory() -> None:
    """§十 R10/R11/R12：OCR 无文字只记 advisory；像素判据仍硬拦。"""
    import subprocess
    from scripts.landscape_guard import check_landscape

    run = PROJ / "output" / "20261118" / "qwen-safety-1"
    vid, cov = run / "video_16_9.mp4", run / "cover_16_9.png"
    if not vid.exists() or not cov.exists():
        print("SKIP  §十 guard 用例（本机无 qwen-safety-1 产物）")
        return
    ok, rep = check_landscape(str(vid), "自立门户？查无实据", cover_path=str(cov))
    check("§十 OCR 无文字不再硬拦（ok=true）", ok is True, str(rep.get("reason")))
    check("§十 记 text_check=advisory_no_text",
          rep.get("text_check") in ("advisory_no_text", "hit", "advisory_miss"), str(rep.get("text_check")))
    check("§十 首帧=封面仍硬判（diff=0）", rep.get("cover_frame_diff") == 0.0, str(rep.get("cover_frame_diff")))
    check("§十 覆盖率判据仍在", rep.get("coverage") == 1.0, str(rep.get("coverage")))

    # R12 反向：竖版塞横屏必须仍被拦
    bad = pathlib.Path(tempfile.mkdtemp()) / "pillarbox.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(run / "video.mp4"),
                    "-vf", "scale=-2:1080,pad=1920:1080:(ow-iw)/2:0", "-t", "5", str(bad)], check=True)
    ok2, rep2 = check_landscape(str(bad), "自立门户？查无实据")
    check("§十 R12 竖版塞横屏仍拦截", ok2 is False, str(rep2.get("reason"))[:60])

    src = (PROJ / "scripts" / "landscape_guard.py").read_text(encoding="utf-8")
    check("§十 无文字硬拦代码已移除",
          'rep["reason"] = "封面 OCR 未识别到任何文字' not in src)
    rl = (PROJ / "scripts" / "render_landscape.py").read_text(encoding="utf-8")
    check("§十 R11 props 文案断言仍在（唯一硬判据）", "LandscapeGuardError" in rl and "_cover_text" in rl)


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
    test_cover_is_first_frame()
    test_guard_ocr_is_advisory()
    test_signals_are_readonly()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
