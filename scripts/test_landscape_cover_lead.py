#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""横版「封面帧贴片头 0.5s」回归（2026-11-18 P1）。

用户实测：横版 video_16_9.mp4 30.485s 与正片等长（没贴），竖版 30.985s 才是 +0.5s。
要求：横版也贴，且**不能把 landscape_guard 的帧指标带偏**。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def _mk_video(path: pathlib.Path, seconds: float = 2.0) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x102030:s=320x180:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


def _mk_cover(path: pathlib.Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=320x180:d=1",
         "-frames:v", "1", str(path)],
        check=True,
    )


def main() -> int:
    from src.pipeline.executors import (
        COVER_LEAD_SECONDS,
        _already_leads_with_cover,
        _assert_has_audio,
        _prepend_cover,
        _probe_duration,
    )

    tmp = pathlib.Path(tempfile.mkdtemp())
    video, cover = tmp / "video_16_9.mp4", tmp / "cover_16_9.png"
    _mk_video(video)
    _mk_cover(cover)

    before = _probe_duration(video)
    check("贴前未带片头", _already_leads_with_cover(video, cover) is False, f"{before:.3f}s")

    _prepend_cover(video, cover)
    after = _probe_duration(video)
    check("时长 +0.5s", abs((after - before) - COVER_LEAD_SECONDS) < 0.12,
          f"{before:.3f}s → {after:.3f}s Δ={after - before:.3f}s")

    body = tmp / "video_body.mp4"
    check("正片留存 video_body.mp4", body.exists() and abs(_probe_duration(body) - before) < 0.12)

    check("幂等判据能识别已贴片头", _already_leads_with_cover(video, cover) is True)

    first = tmp / "first.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", str(first)], check=True)
    from PIL import Image, ImageChops, ImageStat
    with Image.open(first) as a, Image.open(cover) as b:
        diff = ImageStat.Stat(ImageChops.difference(
            a.convert("L").resize((32, 32)), b.convert("L").resize((32, 32)))).mean[0]
    check("首帧 == 封面", diff <= 8.0, f"平均差 {diff:.2f}")

    _assert_has_audio(video)
    check("贴片头后音轨仍在", True)

    # 幂等：再判一次不能重复贴（duration 不应继续涨）
    if not _already_leads_with_cover(video, cover):
        _prepend_cover(video, cover)
    check("重复调用不会二次贴片", abs(_probe_duration(video) - after) < 0.12,
          f"{_probe_duration(video):.3f}s")

    src = (PROJ / "src/pipeline/executors.py").read_text(encoding="utf-8")
    check("贴片头在 render_landscape 返回之后（guard 指标不受影响）",
          src.index("_already_leads_with_cover(video, cover)") > src.index("info = render_landscape("))

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
