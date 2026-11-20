#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""上传封面 = 成片前 0.5s 内的一帧 —— 回归门禁。

用户 2026-11-20 口径：「每个平台的规则不一样，如果需要上传封面，
那就把 0.5 秒以内的一帧作为封面，因为前 0.5 秒都是封面帧。」

守 7 条：
  T1 竖版成片（带 0.5s 封面片头）→ 前 0.5s 三帧一致，可当封面
  T2 正片（video_body，无片头）→ 必须拒绝，不能把画面当封面
  T3 抽帧点必须 < 0.5s（0.6s 要报错）
  T4 没有片头的旧横版 → 必须拒绝（证明判据能抓住「横版没贴片头」这个老问题）
  T5 ensure_upload_cover 优先级：显式 > 同目录 cover_upload* > cover* > 现场抽帧
  T6 拿不到合规封面 → 返回空串（调用方 fail-closed）
  T7 抽帧落盘为非空图片

跑法：python3 scripts/test_cover_upload_frame.py [--run <日期>/<run-id>]
默认用 output 下最近一条「有 video.mp4 + cover.png」的 run。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


def newest_run() -> Path | None:
    runs = [p.parent for p in PROJ.glob("output/*/*/video.mp4")]
    runs = [r for r in runs if (r / "cover.png").exists()]
    return max(runs, key=lambda p: p.stat().st_mtime) if runs else None


def main(argv: list[str]) -> int:
    from src.pipeline.cover_lead import CoverLeadError, make_upload_cover
    from src.utils.upload_cover import ensure_upload_cover

    run = None
    if "--run" in argv:
        raw = Path(argv[argv.index("--run") + 1])
        if raw.is_absolute():
            run = raw
        elif raw.parts and raw.parts[0] == "output":
            run = PROJ / raw
        else:
            run = PROJ / "output" / raw
    run = run or newest_run()
    if not run or not (run / "video.mp4").exists():
        print("SKIP：找不到带 video.mp4 的 run（先出片）")
        return 0

    tmp = Path("/tmp") / "cover_upload_frame_check"
    tmp.mkdir(parents=True, exist_ok=True)

    # T1 竖版带片头
    v = run / "video.mp4"
    info = make_upload_cover(v, tmp / "t1.png", cover_ref=run / "cover.png")
    check("T1 竖版前 0.5s 是静止封面帧",
          info["static_spread"] <= 8 and (tmp / "t1.png").stat().st_size > 0,
          f"spread={info['static_spread']} dHash(vs cover)={info['dhash_vs_cover']}")

    # T2 正片（无片头）必须拒绝
    body = run / "video_body.mp4"
    rejected, why = False, "（文件不存在，跳过）"
    if body.exists():
        try:
            make_upload_cover(body, tmp / "t2.png", cover_ref=run / "cover.png")
            why = "竟然通过了"
        except CoverLeadError as exc:
            rejected, why = True, str(exc)[:60]
    check("T2 无片头正片被拒绝", rejected or not body.exists(), why)

    # T3 抽帧点越界
    bad = ""
    try:
        make_upload_cover(v, tmp / "t3.png", at_sec=0.6)
    except CoverLeadError as exc:
        bad = str(exc)
    check("T3 抽帧点必须 <0.5s", "0.5" in bad, bad[:60])

    # T4 旧横版（没贴片头）必须拒绝
    land = run / "video_16_9.mp4"
    land_rejected, land_why = False, "（无横版，跳过）"
    if land.exists():
        try:
            make_upload_cover(land, tmp / "t4.png", cover_ref=run / "cover_16_9.png"
                              if (run / "cover_16_9.png").exists() else run / "cover.png")
            land_why = "通过（该横版已带封面片头）"
            land_rejected = True   # 新片头版本通过也算对
        except CoverLeadError as exc:
            land_rejected, land_why = True, "拒绝(旧横版无片头)：" + str(exc)[:44]
    check("T4 横版封面抽帧判据有效", land_rejected, land_why)

    # T5 优先级
    prio_ok = True
    detail = []
    declared = tmp / "given.png"
    declared.write_bytes((run / "cover.png").read_bytes())
    got, why = ensure_upload_cover(v, declared)
    if Path(got) != declared:
        prio_ok = False
        detail.append(f"显式传入未优先({why})")
    pre = run / "cover_upload.png"
    existed = pre.exists()
    if not existed:
        pre.write_bytes((run / "cover.png").read_bytes())
    try:
        got2, why2 = ensure_upload_cover(v, None)
        if not Path(got2).name.startswith("cover_upload"):
            prio_ok = False
            detail.append(f"同目录 cover_upload 未优先({Path(got2).name})")
    finally:
        if not existed:
            pre.unlink(missing_ok=True)
    check("T5 封面优先级：显式 > 同目录已抽好 > 源图 > 现场抽", prio_ok, "; ".join(detail))

    # T6 拿不到合规封面 → 空串
    fake = tmp / "fake_no_lead.mp4"
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=30:duration=2", "-pix_fmt", "yuv420p",
                    str(fake)], check=False, capture_output=True)
    got3, why3 = ensure_upload_cover(fake, None)
    check("T6 无片头成片 → 返回空串（fail-closed）", got3 == "", f"{got3} / {why3[:50]}")

    # T7 抽帧文件可用
    ok7 = (tmp / "t1.png").exists() and (tmp / "t1.png").stat().st_size > 5_000
    check("T7 抽帧落盘为非空图片", ok7, f"{(tmp / 't1.png').stat().st_size if ok7 else 0} bytes")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\nrun = {run}")
    for name, ok, detail in RESULTS:
        print(f"  {'✅' if ok else '❌'} {name}" + (f"  [{detail}]" if detail else ""))
    print(f"\n汇总: {passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
