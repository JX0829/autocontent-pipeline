#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竖横并行贴片头不互踩（2026-11-18 P0 / A28~A30 回归）。

事故：`_prepend_cover` 里临时件写死 `.video_with_cover.mp4`、备份写死 `video_body.mp4`。
竖版 `video.mp4` 与横版 `video_16_9.mp4` 在**同一个 run_root** 并行渲染时互相覆盖，
两个成片一起被写坏（已稳定复现两次）。

本测试真造两份不同画幅的片子（竖 1080×1920 / 横 1920×1080）放在同一目录，
两个线程同时贴片头，然后逐项核对：时长、首帧、备份文件、无残留临时件。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def _mk_video(path: pathlib.Path, w: int, h: int, seconds: float, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def _mk_cover(path: pathlib.Path, w: int, h: int, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d=1",
         "-frames:v", "1", str(path)],
        check=True,
    )


def _first_frame(path: pathlib.Path, out: pathlib.Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-frames:v", "1", str(out)], check=True)


def _diff(a: pathlib.Path, b: pathlib.Path) -> float:
    from PIL import Image, ImageChops, ImageStat
    with Image.open(a) as x, Image.open(b) as y:
        return float(ImageStat.Stat(ImageChops.difference(
            x.convert("L").resize((32, 32)), y.convert("L").resize((32, 32)))).mean[0])


def main() -> int:
    from src.pipeline.executors import (
        COVER_LEAD_SECONDS,
        _prepend_cover,
        _probe_duration,
    )

    run_root = pathlib.Path(tempfile.mkdtemp()) / "run_20261118_demo-1"
    run_root.mkdir(parents=True)

    portrait, p_cover = run_root / "video.mp4", run_root / "cover.png"
    land, l_cover = run_root / "video_16_9.mp4", run_root / "cover_16_9.png"
    _mk_video(portrait, 360, 640, 2.0, "0x203040")
    _mk_video(land, 640, 360, 3.0, "0x403020")
    _mk_cover(p_cover, 360, 640, "white")
    _mk_cover(l_cover, 640, 360, "yellow")

    before_p, before_l = _probe_duration(portrait), _probe_duration(land)
    errors: list[BaseException] = []

    def run(video: pathlib.Path, cover: pathlib.Path) -> None:
        try:
            _prepend_cover(video, cover)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=run, args=(portrait, p_cover))
    t2 = threading.Thread(target=run, args=(land, l_cover))
    t1.start(); t2.start(); t1.join(); t2.join()

    check("并行贴片头无异常", not errors, str(errors[:1]))

    after_p, after_l = _probe_duration(portrait), _probe_duration(land)
    check("竖版时长 +0.5s", abs((after_p - before_p) - COVER_LEAD_SECONDS) < 0.12,
          f"{before_p:.2f}s → {after_p:.2f}s")
    check("横版时长 +0.5s", abs((after_l - before_l) - COVER_LEAD_SECONDS) < 0.12,
          f"{before_l:.2f}s → {after_l:.2f}s")

    fp, fl = run_root / "_f_p.png", run_root / "_f_l.png"
    _first_frame(portrait, fp)
    _first_frame(land, fl)
    check("竖版首帧 == 竖版封面", _diff(fp, p_cover) <= 8.0, f"avg diff {_diff(fp, p_cover):.2f}")
    check("横版首帧 == 横版封面", _diff(fl, l_cover) <= 8.0, f"avg diff {_diff(fl, l_cover):.2f}")
    check("竖版首帧 != 横版封面（没串图）", _diff(fp, l_cover) > 8.0)

    body_p, body_l = run_root / "video_body.mp4", run_root / "video_16_9_body.mp4"
    check("竖版备份独立", body_p.exists() and abs(_probe_duration(body_p) - before_p) < 0.12)
    check("横版备份独立", body_l.exists() and abs(_probe_duration(body_l) - before_l) < 0.12)

    leftovers = [p.name for p in run_root.glob(".video*with_cover*")]
    check("无残留临时件", not leftovers, str(leftovers))
    check("旧写死名已不再出现",
          not (run_root / ".video_with_cover.mp4").exists())

    shutil.rmtree(run_root.parent, ignore_errors=True)
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
