#!/usr/bin/env python3
# DEPRECATED: A1–A3 断言无法识别「竖版封面塞横屏」（首帧与封面同图，像素差恒 0），
#   已由 scripts/landscape_guard.check_landscape（像素列覆盖率 + 左右边缘留白 + 首帧=封面）取代。
#   横屏请统一走 scripts/render_landscape.py::render_landscape()。本文件仅作历史参考，不再接线。
"""
scripts/render_landscape_bilibili.py
B 站横屏成片统一渲染入口（**已废弃，见文件头 DEPRECATED**）

职责：
1. 横屏视频统一使用 BilibiliLandscapeVideo（1920x1080，内嵌 0.5s 封面定格）；
2. 横屏封面统一使用 BilibiliCoverStill（1920x1080）；
3. 渲染后执行三项出厂断言，任一不通过即抛异常，绝不产出"看起来对"的坏资产。

三项出厂断言：
  A1 视频分辨率 == 1920x1080
  A2 封面分辨率 == 1920x1080
  A3 第 0 帧画面与封面一致（0.5s 封面定格生效），且视频时长 >= 0.5s

用法：
  python3 scripts/render_landscape_bilibili.py --task-id <task_id>
  python3 scripts/render_landscape_bilibili.py --task-id <id> --props ./public/tasks/<id>/aligned_render.json
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RENDERER_DIR = PROJECT_ROOT / "renderer"

VIDEO_COMP = "BilibiliLandscapeVideo"
COVER_COMP = "BilibiliCoverStill"
EXPECT_W, EXPECT_H = 1920, 1080
EXPECT_MIN_SEC = 0.5
FRAME_DIFF_THRESHOLD = 26.0   # 平均像素差阈值（0~255），超过即判定首帧与封面不一致


class AssertionFailed(RuntimeError):
    pass


def _probe_dims(path: str) -> tuple:
    """用 ffprobe 读取视频宽高"""
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", path
    ])
    st = json.loads(out)["streams"][0]
    return int(st["width"]), int(st["height"])


def _probe_duration(path: str) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path
    ])
    return float(json.loads(out)["format"]["duration"])


def _image_dims(path: str) -> tuple:
    from PIL import Image
    with Image.open(path) as im:
        return im.width, im.height


def _mean_abs_diff(img_a: str, img_b: str) -> float:
    """两张图缩放到同尺寸后计算平均绝对像素差"""
    from PIL import Image, ImageChops
    a = Image.open(img_a).convert("RGB").resize((320, 180))
    b = Image.open(img_b).convert("RGB").resize((320, 180))
    diff = ImageChops.difference(a, b)
    hist = diff.histogram()
    total = 0
    count = 0
    for band in range(3):
        band_hist = hist[band * 256:(band + 1) * 256]
        for value, freq in enumerate(band_hist):
            total += value * freq
            count += freq
    return total / max(count, 1)


def _extract_frame0(video_path: str, out_png: str) -> None:
    subprocess.check_call([
        "ffmpeg", "-y", "-v", "error", "-i", video_path,
        "-vf", "select=eq(n\\,0)", "-vframes", "1", out_png
    ])


def run_assertions(video_path: str, cover_path: str, work_dir: Path) -> Dict[str, Any]:
    """执行三项出厂断言，任一失败抛 AssertionFailed"""
    report: Dict[str, Any] = {}

    # A1 视频分辨率
    vw, vh = _probe_dims(video_path)
    report["A1_video_dims"] = f"{vw}x{vh}"
    if (vw, vh) != (EXPECT_W, EXPECT_H):
        raise AssertionFailed(f"A1 视频分辨率断言失败: 期望 {EXPECT_W}x{EXPECT_H}，实际 {vw}x{vh}")

    # A2 封面分辨率
    cw, ch = _image_dims(cover_path)
    report["A2_cover_dims"] = f"{cw}x{ch}"
    if (cw, ch) != (EXPECT_W, EXPECT_H):
        raise AssertionFailed(f"A2 封面分辨率断言失败: 期望 {EXPECT_W}x{EXPECT_H}，实际 {cw}x{ch}")

    # A3 首帧 == 封面 且 时长 >= 0.5s
    dur = _probe_duration(video_path)
    frame0 = work_dir / "assert_frame0.png"
    _extract_frame0(video_path, str(frame0))
    diff = _mean_abs_diff(str(frame0), cover_path)
    report["A3_duration_sec"] = round(dur, 2)
    report["A3_frame0_vs_cover_diff"] = round(diff, 2)
    if dur < EXPECT_MIN_SEC:
        raise AssertionFailed(f"A3 时长断言失败: 期望 >= {EXPECT_MIN_SEC}s，实际 {dur:.2f}s")
    if diff > FRAME_DIFF_THRESHOLD:
        raise AssertionFailed(
            f"A3 首帧封面断言失败: 第 0 帧与封面平均像素差 {diff:.2f} > {FRAME_DIFF_THRESHOLD}，"
            f"0.5s 封面定格未生效或组件错配"
        )

    report["all_passed"] = True
    return report


def render_landscape(
    task_id: str,
    props_rel: Optional[str] = None,
    output_dir: Optional[str] = None,
    update_db: bool = True,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    渲染 B 站横屏成片 + 封面，并执行三项出厂断言。

    :param task_id: 任务 ID
    :param props_rel: 相对 renderer 目录的 props 路径，默认 ./public/tasks/<task_id>/aligned_render.json
    :param output_dir: 输出目录，默认 output/<yyyymmdd>/<task_id>
    :return: {"video": path, "cover": path, "assertions": report}
    """
    props_rel = props_rel or f"./public/tasks/{task_id}/aligned_render.json"
    props_abs = RENDERER_DIR / props_rel.lstrip("./")
    if not props_abs.exists():
        raise FileNotFoundError(f"props 不存在: {props_abs}")

    if output_dir is None:
        import datetime
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        output_dir = str(PROJECT_ROOT / "output" / date_str / task_id)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    video_path = str(out / f"{task_id}_video_landscape_16_9.mp4")
    cover_path = str(out / f"{task_id}_cover_landscape_16_9.png")

    if verbose:
        print(f"[1/3] 渲染横屏视频 {VIDEO_COMP} -> {video_path}")
    # 清空 Remotion 缓存，杜绝旧资产污染
    subprocess.run(["rm", "-rf", str(RENDERER_DIR / ".cache")], check=False)
    subprocess.check_call([
        "npx", "remotion", "render", "src/index.ts", VIDEO_COMP, video_path,
        f"--props={props_rel}", "--codec=h264", "--crf=18", "--pixel-format=yuv420p"
    ], cwd=RENDERER_DIR)

    if verbose:
        print(f"[2/3] 渲染横屏封面 {COVER_COMP} -> {cover_path}")
    subprocess.check_call([
        "npx", "remotion", "still", "src/index.ts", COVER_COMP, cover_path,
        f"--props={props_rel}", "--image-format=png"
    ], cwd=RENDERER_DIR)

    if verbose:
        print("[3/3] 执行三项出厂断言 A1/A2/A3 ...")
    report = run_assertions(video_path, cover_path, out)
    if verbose:
        print(f"✅ 出厂断言全部通过: {json.dumps(report, ensure_ascii=False)}")

    if update_db:
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from src.db import PipelineDB
            db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE content_tasks SET video_landscape_path=?, cover_landscape_path=?, "
                    "updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
                    (video_path, cover_path, task_id),
                )
                conn.commit()
            if verbose:
                print("✅ 已回写 video_landscape_path / cover_landscape_path")
        except Exception as e:
            if verbose:
                print(f"⚠️ 数据库回写失败（不影响成片）: {e}")

    return {"video": video_path, "cover": cover_path, "assertions": report}


def main():
    ap = argparse.ArgumentParser(description="B站横屏统一渲染入口（含三项出厂断言）")
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--props", default=None)
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    res = render_landscape(
        task_id=args.task_id,
        props_rel=args.props,
        output_dir=args.output_dir,
        update_db=not args.no_db,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
