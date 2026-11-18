#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""封面片头回归：封面帧必须贴在成片最前面播 ~0.5s，且口播不脱轨。

背景（2026-11-18 用户要求）：
  「把你生成的封面帧直接贴到视频最前面，然后播放 0.5 秒」
  「后面不需要单独做封面上传平台，现在抖音平台一直没办法上传封面」

实现：`src/pipeline/executors.py::_prepend_cover` —— 封面图 loop 0.5s + 正片 concat，
音频 adelay 0.5s 保持同步。抖音/视频号都取首帧当封面，于是不需要任何平台上传统计。

本脚本用**真实 ffmpeg 合成**验证四件事（无网络、约 3 秒）：
  ① 时长正好 +0.5s
  ② 首帧与封面图视觉一致（PSNR ≥ 35 dB，实测 42 dB）
  ③ 正片内容整体后移 0.5s（0.6s 处帧 == 原片 0.1s 处帧，PSNR ≥ 40）
  ④ 前 0.45s 静音、0.6s 起有声（adelay 生效，口播不脱轨）
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

FPS = 30
LEAD = 0.5
W, H = 1080, 1920

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{(' ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def psnr(a: Path, b: Path) -> float:
    proc = _run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
                 "-lavfi", "psnr", "-f", "null", "-"])
    for line in (proc.stderr or "").splitlines():
        if "average:" in line:
            raw = line.split("average:")[1].strip().split()[0]
            try:
                return float(raw)
            except ValueError:
                return 99.0 if raw.startswith("inf") else 0.0
    return 0.0


def mean_volume(path: Path, ss: float, t: float) -> float:
    proc = _run(["ffmpeg", "-hide_banner", "-ss", f"{ss}", "-t", f"{t}", "-i", str(path),
                 "-af", "volumedetect", "-f", "null", "-"])
    for line in (proc.stderr or "").splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split()[0])
    return 0.0


def main() -> int:
    from src.pipeline.executors import _prepend_cover, _probe_duration, _probe_size  # noqa: E402

    tmp = Path(tempfile.mkdtemp(prefix="coverprepend_"))
    try:
        # 造样本：3s 纯色片 + 正弦音轨（不依赖任何 run 产物）
        body = tmp / "body.mp4"
        proc = _run(["ffmpeg", "-y", "-v", "error",
                     "-f", "lavfi", "-i", f"color=c=0x102030:s={W}x{H}:r={FPS}:d=3",
                     "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                     "-shortest", str(body)])
        check("样本视频生成", proc.returncode == 0 and body.exists(), f"{body.stat().st_size if body.exists() else 0}B")

        cover = tmp / "cover.png"
        proc = _run(["ffmpeg", "-y", "-v", "error",
                     "-f", "lavfi", "-i", f"color=c=0xFF3366:s={W}x{H}",
                     "-frames:v", "1", str(cover)])
        check("样本封面生成", proc.returncode == 0 and cover.exists())

        video = tmp / "video.mp4"
        shutil.copy2(body, video)
        before = _probe_duration(video)

        _prepend_cover(video, cover, seconds=LEAD)
        after = _probe_duration(video)

        # ① 时长
        delta = after - before
        check("① 时长 +0.5s", abs(delta - LEAD) < 0.12, f"{before:.2f}s → {after:.2f}s（Δ{delta:+.2f}s）")

        # ② 首帧 == 封面
        f0 = tmp / "f0.png"
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", str(f0)])
        p0 = psnr(f0, cover)
        check("② 首帧即封面", p0 >= 35, f"PSNR {p0:.1f} dB（≥35 判通过）")

        # ③ 正片后移 0.5s
        n06, b01 = tmp / "n06.png", tmp / "b01.png"
        _run(["ffmpeg", "-y", "-v", "error", "-ss", "0.6", "-i", str(video), "-frames:v", "1", str(n06)])
        _run(["ffmpeg", "-y", "-v", "error", "-ss", "0.1", "-i", str(body), "-frames:v", "1", str(b01)])
        p3 = psnr(n06, b01)
        check("③ 正片整体后移 0.5s", p3 >= 40, f"0.6s 处帧 vs 原片 0.1s 帧 PSNR {p3:.1f} dB")

        # ④ 音频后移（前段静音、后段有声）
        quiet = mean_volume(video, 0.0, 0.45)
        loud = mean_volume(video, 0.6, 1.0)
        check("④ 口播不脱轨（前静音/后有声）", quiet < -60 and loud > -40,
              f"0~0.45s {quiet:.0f} dB / 0.6s起 {loud:.0f} dB")

        # ⑤ 封面缺失必须显式失败
        from src.pipeline.executors import ExecutorError  # noqa: E402
        try:
            _prepend_cover(video, tmp / "nope.png")
            check("⑤ 缺封面显式失败", False, "没抛异常")
        except ExecutorError as exc:
            check("⑤ 缺封面显式失败", "COVER_PREPEND_FAILED" in str(exc), str(exc)[:48])

        print(f"\nCOVER PREPEND {'PASS' if not fails else 'FAIL'} "
              f"({5 - len(fails)} PASS / {len(fails)} FAIL)")
        return 0 if not fails else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
