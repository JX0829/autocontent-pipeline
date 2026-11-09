#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.external_truth（gate, block）—— 外部事实校验。

"成功"必须以磁盘上的真实文件为准，不能以函数返回值或日志为准：
  1. 声明的每个 artifact 必须存在且非空
  2. 视频类产物必须能被 ffprobe 解码、且有视频流
  3. 封面类产物必须能被 ffprobe 读出尺寸
任一不成立 → block（防"报告成功、实际没出片"）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def main() -> None:
    p = C.load_payload()
    a = C.arts(p)
    if not a:
        C.fail("external_truth: payload 未声明任何 artifacts，无法校验（不接受空声明）")

    checked, problems = [], []
    for key in a:
        path = C.artifact(p, key)
        if path is None:
            problems.append(f"{key}: 路径为空")
            continue
        if not path.exists():
            problems.append(f"{key}: 文件不存在 {path}")
            continue
        size = path.stat().st_size
        if size <= 0:
            problems.append(f"{key}: 文件为 0 字节")
            continue
        suffix = path.suffix.lower()
        if suffix in VIDEO_EXT:
            info = C.ffprobe_json(path)
            if not info or not any(s.get("codec_type") == "video" for s in info.get("streams") or []):
                problems.append(f"{key}: 无法解码出视频流")
                continue
        elif suffix in IMAGE_EXT:
            if not C.video_size(path):
                problems.append(f"{key}: 图片无法读出尺寸（损坏）")
                continue
        checked.append({"key": key, "size": size, "path": str(path)})

    if problems:
        C.fail("external_truth: " + "; ".join(problems[:5]), problems=problems[:10])

    C.emit(True, patch={"external_truth": "ok", "verified": checked})


if __name__ == "__main__":
    main()
