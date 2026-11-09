#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.frame_fingerprint（transform）—— 视觉指纹（T2.4）

取 **3 帧**（0.5s / 中段 / 末-1s）→ 每帧 `scale=32:32` 灰度 → 拼起来取 md5[:16]。
缩放到 32x32 是为了让指纹对分辨率/码率差异不敏感，但仍能区分"换了片子"。

产出同时写库：`artifacts(task_id, kind, path, sha256, frame_hash)`（T2.2 的表），
这样"这条片子是不是重渲染的老片子"可以事后用 SQL 查，而不是靠日志。
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    key = str(c.get("video_key") or "video_16_9")
    video = C.artifact(p, key) or C.artifact(p, "video") or C.artifact(p, "vertical")
    if video is None or not video.exists():
        C.fail(f"frame_fingerprint: 找不到视频产物（artifacts.{key} / video）")
    if video.stat().st_size < 1024:
        C.fail(f"frame_fingerprint: 视频文件过小（{video.stat().st_size} bytes），疑似空文件")

    duration = C.video_duration(video)
    if not duration or duration <= 1.5:
        C.fail(f"frame_fingerprint: 视频时长异常（{duration}），无法取 3 帧采样点")

    timestamps = [0.5, round(duration / 2, 2), round(max(duration - 1.0, 0.6), 2)]
    frames: list[bytes] = []
    per_frame: dict[str, str] = {}
    for ts in timestamps:
        data = C.frame_gray32(video, ts=ts)
        if data is None:
            C.fail(f"frame_fingerprint: {ts}s 抽帧失败（ffmpeg/解码异常）", timestamps=timestamps)
        frames.append(data)
        per_frame[f"{ts}s"] = hashlib.md5(data).hexdigest()[:8]

    frame_fp = hashlib.md5(b"".join(frames)).hexdigest()[:16]
    video_sha = C.sha256_file(video)
    task_id = str(p.get("task_id") or "")
    kind = str(c.get("artifact_kind") or ("landscape" if "16_9" in key else "vertical"))

    if task_id:
        try:
            from src.db import PipelineDB
            PipelineDB(str(C.db_path(p))).record_artifact(
                task_id, kind, video, video_sha, frame_fp)
        except Exception:
            pass  # 指纹本身已算出，写库失败不阻断（台账缺失会在 send_ledger 处失败关闭）

    C.emit(True, patch={
        "frame_fp": frame_fp,
        "frame_fp_full": frame_fp,
        "per_frame": per_frame,
        "timestamps": timestamps,
        "video_sha256": video_sha[:16],
        "artifact_kind": kind,
    })


if __name__ == "__main__":
    main()
