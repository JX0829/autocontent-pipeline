#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
横屏唯一标准入口（方案 C）。

用法：
    from scripts.render_landscape import render_landscape
    info = render_landscape(task_id, props_json, out_dir)

约定（任何横屏渲染都必须走这里，杜绝再选错 composition）：
  · 视频：BilibiliLandscapeVideo（专用横屏组件，封面为 BilibiliLandscapeCover 的 16:9 row 布局）
  · 封面：BilibiliCoverStill（1920x1080）
  · 渲染后强制过 landscape_guard 出厂校验，不通过直接抛 RuntimeError，绝不产出可推送的错版
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

PROJ = Path(__file__).resolve().parent.parent
RENDERER = PROJ / "renderer"
sys.path.insert(0, str(PROJ))

VIDEO_COMPOSITION = "BilibiliLandscapeVideo"
COVER_COMPOSITION = "BilibiliCoverStill"


class LandscapeGuardError(RuntimeError):
    pass


def render_landscape(task_id: str, props_json: str | Path | None = None,
                     out_dir: str | Path | None = None,
                     expect_cover_text: str = "",
                     video_composition: str | None = None,
                     cover_composer: "Callable[[Path, Path], Path] | None" = None,
                     cover_composition: str | None = None) -> dict:
    """横屏唯一入口。

    2026-11-12（M2）：新增 video_composition / cover_composition 两个可选覆盖参数，
    供课文故事走古风合成（TextbookLandscapeVideo / TextbookLandscapeCoverStill）。
    缺省仍由本文件顶部常量决定 —— 科技链路的调用方一个字都不用改。
    """
    props_json = Path(props_json) if props_json else PROJ / "renderer/public/tasks" / task_id / "aligned_render.json"
    # 一律绝对路径：remotion 以 renderer/ 为 cwd，相对路径会把产物写歪，随后 ffmpeg 又找不到
    out_dir = (Path(out_dir) if out_dir else PROJ / "output" / task_id).resolve()
    props_json = props_json.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not props_json.exists():
        raise FileNotFoundError(f"props 不存在: {props_json}")

    # 确定性来源校验（替代 OCR 语义判定）：封面主文案必须来自数据，
    # 而不是组件里写死的话术 —— 这条断言不依赖 OCR，稳定可重复（QA#7 根因）。
    if expect_cover_text:
        try:
            _props = json.loads(props_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LandscapeGuardError(f"props 不是合法 JSON: {props_json} ({exc})") from exc
        _cover_text = str(_props.get("cover_main_text") or _props.get("title") or "")
        if not _cover_text.strip():
            raise LandscapeGuardError(
                "props 缺 cover_main_text/title → 封面文案无处可取，必然是组件写死的话术")
        from scripts.hooks import _common as _C
        _want = _C.normalize_text(expect_cover_text)
        _got = _C.normalize_text(_cover_text)
        if _want and _want not in _got and _got not in _want:
            raise LandscapeGuardError(
                f"props 封面文案与期望不一致：props「{_cover_text}」≠ 期望「{expect_cover_text}」")

    # T4.1：props 一律传绝对路径（props 可能落在 run_root 下，不再假设在 renderer/public）
    rel_props = str(props_json.resolve())

    # T4.1 / P-6：产物名与 pipeline.yaml 的 produces / dispatch.reads 对齐
    video = out_dir / "video_16_9.mp4"
    cover = out_dir / "cover_16_9.png"

    video_comp = video_composition or VIDEO_COMPOSITION
    cover_comp = cover_composition or COVER_COMPOSITION

    subprocess.check_call([
        "npx", "remotion", "render", "src/index.ts", video_comp, str(video),
        f"--props={rel_props}", "--codec=h264", "--crf=18", "--pixel-format=yuv420p",
    ], cwd=str(RENDERER))

    # SPEC-20261117 §十一 R13~R18：封面 = 成片截帧 + 渲染文案。
    # 调用方给了 cover_composer（tech_story 链路）就用它；没给（课文本等老链路）仍抽第 0 帧。
    if cover_composer is not None:
        cover = Path(cover_composer(video, cover))
    else:
      # SPEC-20261117 §八（A10）：横版封面 = video_16_9.mp4 第 0 帧，不再单独渲封面构图。
      # 失败必须显式报错（不回落旧 CoverStill、不出空封面）；现场保留 video。
      _ff = subprocess.run(
          ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", "select=eq(n\\,0)",
           "-frames:v", "1", str(cover)],
          capture_output=True, text=True,
      )
      if _ff.returncode != 0 or not cover.exists() or cover.stat().st_size == 0:
          raise LandscapeGuardError(
              "COVER_FRAME_EXTRACT_FAILED: 横版抽第 0 帧失败 "
              f"(rc={_ff.returncode}) {( _ff.stderr or '' ).strip().splitlines()[-2:]} | video={video}")

    from scripts.landscape_guard import check_landscape
    ok, report = check_landscape(str(video), expect_cover_text, cover_path=str(cover),
                                 compare_cover_frame=(cover_composer is None))
    report["cover_path"] = str(cover)
    report["video_path_generated"] = str(video)
    if not ok:
        raise LandscapeGuardError(f"横屏出厂校验失败: {json.dumps(report, ensure_ascii=False)}")
    return report


if __name__ == "__main__":
    tid = sys.argv[1]
    expect = sys.argv[2] if len(sys.argv) > 2 else ""
    print(json.dumps(render_landscape(tid, expect_cover_text=expect), ensure_ascii=False, indent=2))
