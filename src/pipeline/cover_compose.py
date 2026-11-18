#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""封面合成：成片截帧做底图 + tech_story 语汇渲染文案（SPEC-20261117 §十一 R13~R18）。

为什么不直接拿第 0 帧：
  · 第 0 帧是 HookOverlay 的描边大号标题，tesseract 读不出字（§十 P0 就是这么炸的），
    平台缩略图上也辨识不出文案；
  · 老 `CoverStill` / `BilibiliLandscapeCover` 是「海报」版式，与 tech_story 正文不搭（用户否决）。

现在的做法：从**成片里挑一帧有实质画面的**（避开 scene 0 开场）当底图，
再由 `renderer/src/tech/TechCoverCard.tsx` 按 tech_story 的字体/强调色/左下版位把文案渲上去。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

PROJ = Path(__file__).resolve().parent.parent.parent
RENDERER = PROJ / "renderer"
PUBLIC = RENDERER / "public"

logger = logging.getLogger(__name__)

COVER_COMPOSE_FAILED = "COVER_COMPOSE_FAILED"

#: 竖/横版各自的静态构图（同一组件，只换画幅）
COMPOSITION = {
    "portrait": ("TechCoverCardPortrait", (1080, 1920)),
    "landscape": ("TechCoverCardLandscape", (1920, 1080)),
}


class CoverComposeError(RuntimeError):
    """封面合成失败（截帧 / 渲染 / 尺寸校验）。code 固定 COVER_COMPOSE_FAILED。"""


def _fail(stage: str, *, video: Path | str, frame: int, extra: str = "") -> CoverComposeError:
    msg = f"{COVER_COMPOSE_FAILED}: stage={stage} video={video} frame={frame}"
    if extra:
        msg += f" | {extra}"
    return CoverComposeError(msg)


def pick_frame(props: dict[str, Any]) -> int:
    """选底图帧（R13）：避开 scene 0 的 HookOverlay 描边大字段。

    规则：优先 scene 1 起第一个像样的分镜（取其起始 + 12 帧，让场景卡/画面就位）；
    视频太短没有 scene 1 → 退 scene 0 第 16 帧（过 0.5s）；再不行报错。
    """
    scenes = props.get("scenes") or []
    for sc in scenes[1:]:
        start = int(sc.get("start_frame") or 0)
        dur = int(sc.get("duration_frames") or 0)
        if dur >= 20:
            return start + min(12, max(1, dur // 3))
    if scenes:
        # 回退 scene 0 第 16 帧（过 0.5s 描边大字）；是否越界由后面 _video_frames 校验
        return 16
    raise CoverComposeError(f"{COVER_COMPOSE_FAILED}: stage=pick_frame 没有可用分镜（scenes={len(scenes)}）")


#: 竖版封面「内容板」高度：1080x1440 = 3:4，正好是抖音个人主页的裁剪区
PANEL_34_HEIGHT = 1440


def _fit_panel_34(src: Path, dst: Path, *, w: int = 1080, h: int = 1920) -> None:
    """把 9:16 截帧装成「中间 3:4 完整画面 + 上下纯深色」的干净封面底图。

    演进（都是用户当天提的）：
      ① 「上下被裁掉很难看」→ 中间裁成 3:4 完整画面，上下填充；
      ② 「上下内容隐藏，确保图面干净吸引人」→ 填充不再用同图虚化（仍有纹理和色块，
         看着脏），改成**纯深色底** + 画面上下边缘 70px 羽化，融进底色不留硬边。

    结果：抖音 3:4 裁剪区里是满幅清晰画面，裁掉的上下两块只剩干净底色。
    """
    panel = min(PANEL_34_HEIGHT, h)
    y = (h - panel) // 2
    feather = 70
    filt = (
        f"[1:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"crop={w}:{panel}:(iw-{w})/2:(ih-{panel})/2,scale={w}:{panel},"
        f"format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='255*min(1\\,min(Y\\,{panel - 1}-Y)/{feather})'[panel];"
        f"[0:v][panel]overlay=0:{y}:format=auto,format=yuv420p[vout]"
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.unlink(missing_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c=0x05070b:s={w}x{h}",
         "-i", str(src),
         "-filter_complex", filt, "-map", "[vout]", "-frames:v", "1", str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise CoverComposeError(f"{COVER_COMPOSE_FAILED}: stage=panel34 rc={proc.returncode} {tail}")


def pick_clean_source(props: dict[str, Any]) -> tuple[Optional[Path], str]:
    """挑「不含文字」的画面当封面底图（2026-11-18 用户要求）。

    成片截帧这条老路有个硬伤：成片里已经烧了「锐评」和「备忘录/字幕」，
    截出来当封面等于把正文文字重复印在封面上，用户原话「那上边的锐评和下边的
    备忘录是啥，这两个部分要隐藏」。

    所以优先用**素材背景 loop**（设计好的纯背景，无任何文字）：
      assets.cover_bg / assets.bg 指向 renderer/public 下的视频文件时直接用，
      帧号沿用 pick_frame() 的结果（同一时间点，画面风格和正片一致）。
    取不到才退回成片截帧（有文字，但不至于没有封面）。
    """
    candidates: list[Any] = []
    assets = props.get("assets") or {}
    candidates.append(assets.get("cover_bg"))
    # 分镜自带的 AI 素材视频（科技主视觉那条线）天生没有文字，比背景 loop 更有画面感
    for sc in props.get("scenes") or []:
        candidates.append((sc or {}).get("media_asset_url"))
    candidates.append(assets.get("bg"))

    for rel in candidates:
        if not isinstance(rel, str) or not rel.strip():
            continue
        if not rel.lower().endswith((".mp4", ".mov", ".webm", ".m4v")):
            continue
        path = PUBLIC / rel
        if path.exists() and path.stat().st_size > 0:
            return path, rel
    return None, ""


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        w, h = out.split(",")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return (0, 0)


def _video_frames(video: Path) -> int:
    """总帧数（nb_frames 优先，退化用 duration × fps）。判不出返回 0（不做上限校验）。"""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=nb_frames,duration,r_frame_rate", "-of", "json", str(video)],
        capture_output=True, text=True,
    ).stdout
    try:
        st = (json.loads(out).get("streams") or [{}])[0]
        if st.get("nb_frames") and str(st["nb_frames"]).isdigit():
            return int(st["nb_frames"])
        dur = float(st.get("duration") or 0)
        num, _, den = str(st.get("r_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        return int(dur * fps)
    except Exception:  # noqa: BLE001
        return 0


def _extract_frame(video: Path, frame: int, out_png: Path) -> None:
    if not video.exists() or video.stat().st_size == 0:
        raise _fail("video_missing", video=video, frame=frame)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.unlink(missing_ok=True)          # 绝不复用上一次的底图（会静默用旧帧）
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", str(out_png)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise _fail("extract_frame", video=video, frame=frame,
                    extra=f"rc={proc.returncode} {tail}")


def compose_cover(video: Path | str, out_png: Path | str, *, props: dict[str, Any],
                  run_id: str, aspect: str = "portrait",
                  main_text: str = "", sub_text: str = "") -> Path:
    """成片截帧 → 渲染文案 → out_png。失败一律抛 CoverComposeError（R18）。"""
    # 必须绝对路径：remotion still 的 cwd 是 renderer/，相对路径会被写进 renderer/ 下
    video, out_png = Path(video).resolve(), Path(out_png).resolve()
    comp, (want_w, want_h) = COMPOSITION.get(aspect) or COMPOSITION["portrait"]

    main = (main_text or props.get("cover_main_text") or props.get("title") or "").strip()
    if not main:
        raise CoverComposeError(f"{COVER_COMPOSE_FAILED}: stage=text 主文案为空"
                                f"（cover_main_text / title 都没有），拒绝产出无字封面")
    sub = (sub_text or props.get("cover_sub_text") or "").strip()

    frame = pick_frame(props)
    total = _video_frames(video)
    if total and frame >= total:
        raise _fail("frame_out_of_range", video=video, frame=frame,
                    extra=f"视频只有 {total} 帧（残片/损坏），拒绝产出封面")

    # 底图来源：优先无文字的素材背景 loop；成片里烧了锐评/备忘录，只作兜底
    clean_src, clean_rel = pick_clean_source(props)
    src = clean_src or video
    src_frame = frame
    if clean_src:
        loop_total = _video_frames(clean_src)
        if loop_total:
            src_frame = frame % loop_total
        logger.info("[cover] 底图源=%s（无文字素材） frame=%d", clean_rel or str(clean_src), src_frame)
    else:
        logger.warning("[cover] 没有素材背景 loop，退回成片截帧（封面会带上正片文字）")

    base = PUBLIC / "runs" / run_id / f"cover_base_{aspect}.png"
    if aspect == "portrait":
        raw = PUBLIC / "runs" / run_id / "cover_frame_raw.png"
        _extract_frame(src, src_frame, raw)
        _fit_panel_34(raw, base, w=want_w, h=want_h)
    else:
        _extract_frame(src, src_frame, base)

    rel = str(base.relative_to(PUBLIC)).replace("\\", "/")
    payload = {"bg": rel, "main": main, "sub": sub,
               "theme": props.get("theme") or "", "aspect": aspect}
    props_file = PUBLIC / "runs" / run_id / f"cover_props_{aspect}.json"
    props_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.unlink(missing_ok=True)          # 绝不复用上一次的底图（会静默用旧帧）
    proc = subprocess.run(
        ["npx", "remotion", "still", "src/index.ts", comp, str(out_png),
         f"--props={props_file.resolve()}", "--image-format=png"],
        cwd=str(RENDERER), capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise _fail("render_still", video=video, frame=frame,
                    extra=f"composition={comp} rc={proc.returncode} {tail}")

    # 落绑定证据：证明「这张封面」的底图就是该视频的第 frame 帧（供 external_truth hook 校验）
    import hashlib
    binding = out_png.parent / f"cover_binding_{aspect}.json"
    binding.write_text(json.dumps({
        "aspect": aspect, "video": str(video), "frame": frame,
        "base_source": str(src), "base_source_rel": clean_rel, "base_frame": src_frame,
        "base": str(base), "base_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
        "composition": comp, "cover": str(out_png),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    got = _probe_size(out_png)
    if got != (want_w, want_h):
        raise _fail("size_check", video=video, frame=frame,
                    extra=f"封面尺寸 {got} ≠ 期望 {(want_w, want_h)}")
    logger.info("[cover] %s 底图=v%d 尺寸=%s 文案=%r", out_png.name, frame, f"{got[0]}x{got[1]}", main[:24])
    return out_png


def main(argv: list[str]) -> int:
    """手工补封面：python3 -m src.pipeline.cover_compose <run_id> [portrait|landscape]"""
    logging.basicConfig(level=logging.INFO)
    if len(argv) < 2:
        print(__doc__)
        return 2
    run_id = argv[1]
    aspect = argv[2] if len(argv) > 2 else "portrait"
    run_root = PROJ / "output"
    hits = list(run_root.glob(f"*/{run_id}"))
    if not hits:
        print(f"找不到 run: {run_id}")
        return 2
    rr = hits[0]
    props = json.loads((rr / "props" / "aligned_render.json").read_text(encoding="utf-8"))
    video = rr / ("video.mp4" if aspect == "portrait" else "video_16_9.mp4")
    out = rr / ("cover.png" if aspect == "portrait" else "cover_16_9.png")
    try:
        p = compose_cover(video, out, props=props, run_id=run_id, aspect=aspect)
    except Exception as exc:  # noqa: BLE001
        print(f"失败: {exc}")
        return 1
    print(f"OK {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
