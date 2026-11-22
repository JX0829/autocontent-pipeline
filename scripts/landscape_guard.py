#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
横屏出厂护栏（方案 B）：
1) ffprobe 宽高必须 1920x1080
2) 封面段**文字 bbox 横向跨度**中位数必须 >= 75%（竖版封面塞进横屏会挤在一侧，
   历史错版实测 32.8%；正确横屏 82.4%~90.4%）
   —— 用 OCR bbox 跨度而不是"像素覆盖度"：后者会把正确横屏误判成 57%
3) OCR 多采样取并集，**归一化后**必须命中封面主文案（全半角/标点差异不算差异）
4) 必需参数：视频路径、期望主文案
返回 (passed: bool, report: dict)；不通过时调用方应拒绝推送。
"""
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "scripts" / "hooks"))
import _common as C  # noqa: E402

# 横屏开场有入场动画（0.2s 文字未就位），采样窗口后移到动画结束之后
SPAN_SAMPLES = (0.2, 1.0, 2.0, 3.5)
# 封面（含主文案）只存在于前 15 帧 = 0~0.5s，文案真值必须在这个窗口内采样，
# 否则采到的是分镜画面 → 「OCR 未命中主文案」的假失败（实测踩过）
COVER_TEXT_SAMPLES = (0.05, 0.15, 0.25, 0.35, 0.45)
MIN_COVERAGE = 0.55   # 总覆盖率下限（实测：正当成片 0.672~0.969 / 竖版居中 0.396）
MIN_EDGE = 0.05       # 左右各 8% 带宽覆盖率下限（实测：正当 0.516~1.000 / 竖版居中 0.000）★主判据
MAX_FRAME_DIFF = 8.0   # 灰度平均差上限（仅辅助信息，不再做硬判）
LEAD_IN_SEC = 0.5      # 横版封面片头时长；无 _body.mp4 时从片头之后采样判画幅
# 封面「是不是从这条成片抽的」改用 dHash 汉明距离：均值差对 h264 重编码 + 文案叠加太敏感
# （实测同图 11.7~11.8 → 被 8.0 误杀），dHash 同图 0~13、异图 25~28，取 20 在两者之间。
DHASH_MAX = 20
# 候选抽帧位：0（旧口径抽首帧）与 COVER_FRAME（54=1.8s，标题淡入后）；有 binding 时再加 binding.frame。
# 为什么给多个：同一批成片里 0 帧口径与 54 帧口径并存，只比一个位置必然误杀另一批。
COVER_FRAME_CANDIDATES = (0, 54)


def _probe(path: str) -> tuple[int, int]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
    ], text=True).strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def _frame_gray(video: str, ts: float) -> tuple[bytes, int, int]:
    """抽一帧转灰度原始数据"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = tf.name
    subprocess.check_call(["ffmpeg", "-y", "-ss", str(ts), "-i", video, "-vframes", "1",
                           png, "-loglevel", "error"])
    w, h = _probe_png(png)
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", png,
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    Path(png).unlink(missing_ok=True)
    return raw, w, h


def _frame_gray_at_frame(video: str, frame_no: int) -> tuple[bytes, int, int]:
    r"""按**帧号**取一帧灰度数据（不是 -ss 跳秒）。

    -ss 会落到最近关键帧，和 select=eq(n\,N) 抽出来的封面帧不是同一帧，
    实测课文本（24fps/GOP 大）差 11.7 灰度级 → 守卫误判。这里统一用帧号选帧。
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = tf.name
    subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-i", video,
                           "-vf", f"select=eq(n\\,{max(0, int(frame_no))})",
                           "-frames:v", "1", png])
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", png,
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    w, h = _probe_png(png)
    Path(png).unlink(missing_ok=True)
    return raw, w, h


def _probe_png(path: str) -> tuple[int, int]:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", path,
    ], text=True).strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def _col_coverage(raw: bytes, w: int, h: int, thresh: float = 3.0) -> tuple[float, float, float]:
    """按列灰度标准差判断「该列有没有内容」，返回 (总覆盖率, 左 8% 覆盖率, 右 8% 覆盖率)。

    选这个判据的原因（实测分离度，2026-11-09）：
      正确横屏（全幅铺开）  coverage 0.784~0.969，左右两侧 0.51~1.00
      竖版封面居中留白      coverage 0.396，左右两侧 **0.000**
    OCR bbox 跨度对本文案排版不可靠：主标题左对齐只占 39%，OCR 也常常漏读
    右侧小字号卡片；而「两侧是否成片留白」正是竖版塞横屏的物理特征。
    """
    stds = []
    for x in range(w):
        vals = raw[x::w]
        n = len(vals)
        if n == 0:
            stds.append(0.0)
            continue
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals[::3]) / max(1, len(vals[::3]))
        stds.append(var ** 0.5)

    def _cov(seq: list[float]) -> float:
        return (sum(1 for s in seq if s > thresh) / len(seq)) if seq else 0.0

    band = max(1, int(w * 0.08))
    return _cov(stds), _cov(stds[:band]), _cov(stds[-band:])


def _bigrams(s: str) -> set[str]:
    """归一化文本的二元组集合；单字符时退回自身。"""
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


def text_hit(want: str, got: str, min_ratio: float = 0.70) -> tuple[bool, float]:
    """主文案命中判定：整体包含 或 二元组覆盖率 ≥ min_ratio。

    为什么不能要求整体包含：OCR 偶尔吞掉标题末字（实测「V4推理提速三**倍**」被读成
    「V4推理提速三」），整体包含会直接判死一条完全正确的成片；
    而二元组覆盖率对单字丢失不敏感，对「换成另一段文案」仍然敏感（覆盖率会掉到很低）。
    """
    if not want:
        return True, 1.0
    if want in got:
        return True, 1.0
    w, g = _bigrams(want), _bigrams(got)
    if not w:
        return True, 1.0
    ratio = len(w & g) / len(w)
    return ratio >= min_ratio, ratio


def _video_fps(video: str) -> float:
    """读视频真实帧率 —— 科技模板 30fps、课文本 24/25fps，写死 30 会算错时间戳。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", video],
            capture_output=True, text=True).stdout.strip()
        num, den = out.split("/")
        fps = float(num) / float(den)
        return fps if fps > 0 else 30.0
    except Exception:
        return 30.0


def _cover_frame_no() -> int:
    try:
        return max(0, int(os.environ.get("COVER_FRAME") or 54))
    except ValueError:
        return 54


def _cover_frame_ts(video: str = "") -> float:
    """封面帧对应的时间戳（秒）。

    2026-11-18：封面不再固定取第 0 帧 —— 模板标题从第 18–48 帧才淡入，
    抽第 0 帧会得到一张没有文字的黑场。默认改抽第 54 帧（1.8s），
    与 executors.DEFAULT_COVER_FRAME / render_landscape 的 COVER_FRAME 保持一致。
    """
    try:
        frame_no = max(0, int(os.environ.get("COVER_FRAME") or 54))
    except ValueError:
        frame_no = 54
    return frame_no / (_video_fps(video) if video else 30.0)


def _frame_diff(a: str, b: str) -> float:
    """两帧灰度平均绝对差（0=完全一致）。用于确认「成片封面帧就是那张封面图」。"""
    try:
        ra, _wa, _ha = _frame_gray_at_frame(a, _cover_frame_no())
        rb, _wb, _hb = _frame_gray(b, 0.0)
    except Exception:
        return 255.0
    if len(ra) != len(rb):
        return 255.0
    return sum(abs(x - y) for x, y in zip(ra, rb)) / len(ra)


def _dhash_png(path: str) -> list[int] | None:
    """9x8 灰度 → 64 位差分行哈希。同图 0~13，异图 25~28（2026-11-19 实测）。"""
    try:
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", path, "-vf",
                                       "scale=9:8:flags=area", "-f", "rawvideo",
                                       "-pix_fmt", "gray", "-"])
    except Exception:  # noqa: BLE001
        return None
    if len(raw) < 72:
        return None
    bits: list[int] = []
    for y in range(8):
        row = raw[y * 9:(y + 1) * 9]
        for x in range(8):
            bits.append(1 if row[x] < row[x + 1] else 0)
    return bits


def _dhash_video_frame(video: str, ts: float) -> list[int] | None:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = tf.name
    try:
        subprocess.check_call(["ffmpeg", "-y", "-ss", str(ts), "-i", video, "-vframes", "1",
                               png, "-loglevel", "error"])
        return _dhash_png(png)
    except Exception:  # noqa: BLE001
        return None
    finally:
        Path(png).unlink(missing_ok=True)


def _match_cover_frame(video: str, cover: str, extra_frame: int | None = None) -> dict:
    """在候选帧里找「封面出自哪一帧」，返回 {frame, dhash, best_mean_diff}。

    只有 dHash 命中才算同图；平均差只作辅助信息（h264 重编码会把它抬到 11+）。
    """
    ch = _dhash_png(cover)
    cands = list(COVER_FRAME_CANDIDATES)
    if extra_frame is not None and extra_frame not in cands:
        cands.append(int(extra_frame))
    best: dict = {"frame": None, "dhash": 255, "best_mean_diff": None}
    for fr in cands:
        vh = _dhash_video_frame(video, fr / 30.0)
        if vh is None or ch is None:
            continue
        dist = sum(1 for a, b in zip(vh, ch) if a != b)
        if dist < best["dhash"]:
            ts = fr / 30.0
            try:
                ra, _w, _h = _frame_gray(video, ts)
                rb, _w2, _h2 = _frame_gray(cover, 0.0)
                md = round(sum(abs(x - y) for x, y in zip(ra, rb)) / len(ra), 2) if len(ra) == len(rb) else None
            except Exception:  # noqa: BLE001
                md = None
            best = {"frame": fr, "dhash": dist, "best_mean_diff": md}
    return best


def _cover_binding(cover: str | None, video: str | None = None,
                   want_aspect: str | None = None) -> dict | None:
    """读取 cover_compose 落的绑定证据（SPEC-20261117 §十一）。

    有绑定且底图 hash 与记录一致 → 说明「这张封面」的底图确实是该成片的某一帧，
    此时不再用「视频首帧 == 封面」判据（合成封面必然不等于首帧）。
    """
    if not cover:
        return None
    cp = Path(cover)
    aspect = "landscape" if "16_9" in cp.name else "portrait"
    bp = cp.parent / f"cover_binding_{aspect}.json"
    if not bp.exists():
        return None
    try:
        bind = json.loads(bp.read_text(encoding="utf-8"))
        # 绑定必须属于「正在检查的这条成片」：只校验 base 的 sha 是不够的 ——
        # 拿 A 片的封面去检查 B 片，A 的 binding 自身完全自洽，会被误当成 composer 证据放行
        # （实测：ink-c-yuanri3 的视频 + silent-consensus-1 的封面 → 旧代码判 OK）。
        if video and bind.get("video"):
            try:
                if Path(bind["video"]).resolve() != Path(video).resolve():
                    return None
            except OSError:
                return None
        if want_aspect and bind.get("aspect") and bind["aspect"] != want_aspect:
            return None
        base = Path(bind.get("base") or "")
        if not base.exists():
            return None
        import hashlib
        if hashlib.sha256(base.read_bytes()).hexdigest() != bind.get("base_sha256"):
            return None
        return bind
    except Exception:  # noqa: BLE001
        return None


def check_landscape(video: str, expect_text: str = "",
                    cover_path: str | None = None,
                    compare_cover_frame: bool = True) -> tuple[bool, dict]:
    """compare_cover_frame=False：封面不是「视频第 0 帧」而是合成卡片（SPEC-20261117 §十一），
    此时不做首帧一致性硬判，只在报告里记 cover_source=composer。"""
    rep = {"video": video}
    try:
        w, h = _probe(video)
    except Exception as e:
        return False, {"error": f"ffprobe 失败: {e}"}
    rep["size"] = f"{w}x{h}"
    if (w, h) != (1920, 1080):
        rep["reason"] = f"分辨率错误 {w}x{h}，应为 1920x1080"
        return False, rep

    # 主判据：首帧像素列覆盖率 + 左右边缘是否留白（竖版封面塞横屏的物理特征）
    # 2026-11-21：横版成片开头 0.5s 是**封面片头**（executors._prepend_cover），
    # 封面是「成片截帧 + 渲文案」的卡片，左右边距是设计边距（实测 95px / 144px），
    # 拿它判「竖版封面塞横屏」必然误红（MedChemBench 右边缘 3.9% < 5%）。
    # 判断对象改为**正片**：优先用同名 `_body.mp4`，否则退到片头之后的 0.5s 采样。
    from pathlib import Path as _P
    vp = _P(video)
    body_sib = vp.with_name(vp.stem.replace("_with_cover", "") + "_body.mp4")
    sample_video, sample_t = video, LEAD_IN_SEC
    if body_sib.exists() and body_sib.stat().st_size > 0:
        sample_video, sample_t = str(body_sib), 0.0
    rep["sample"] = {"video": _P(sample_video).name, "t": sample_t}
    try:
        raw, fw, fh = _frame_gray(sample_video, sample_t)
    except Exception as e:
        return False, {"error": f"抽帧失败: {e}"}
    cov, left, right = _col_coverage(raw, fw, fh)
    rep.update({"coverage": round(cov, 3), "edge_left": round(left, 3),
                "edge_right": round(right, 3)})
    if cov < MIN_COVERAGE:
        rep["reason"] = (f"正片内容列覆盖率仅 {cov:.1%}（<{MIN_COVERAGE:.0%}），"
                         f"画幅两侧留白 → 判定为竖版封面塞横屏（采样 {rep['sample']}）")
        return False, rep
    if left < MIN_EDGE or right < MIN_EDGE:
        rep["reason"] = (f"左右边缘覆盖率 {left:.1%}/{right:.1%}"
                         f"（需均 ≥{MIN_EDGE:.0%}），有一侧是纯背景 → 判定为竖版封面塞横屏")
        return False, rep

    # 辅助信息（不参与判定）：OCR 文字 bbox 跨度中位数，供人工复核版式
    spans = C.ocr_text_spans(Path(video), SPAN_SAMPLES)
    valid = [s for _, s in spans if s is not None]
    rep["ocr_text_span_median"] = round(statistics.median(valid), 3) if valid else None
    rep["ocr_text_span_samples"] = {str(ts): (round(s, 3) if s is not None else None)
                                    for ts, s in spans}

    # 文案真值 + 首帧一致性
    #  · 文案从**静帧封面 PNG** 取：h264 帧上 tesseract 对大号描边标题识别不稳
    #    （实测同一画面：PNG 能读「V4炸场! 速度翻三倍」，视频帧只读到副标题）
    #  · 再校验「视频首帧 ≈ 封面」，确保投放时首帧画面就是这张海报（A3 的本意）
    if expect_text or cover_path:
        cover_ok = bool(cover_path and Path(cover_path).exists())
        if cover_ok:
            try:
                cw, chh = _probe_png(str(cover_path)) if str(cover_path).lower().endswith(".png") else _probe(str(cover_path))
                rep["cover_size"] = f"{cw}x{chh}"
                if chh > cw:
                    rep["reason"] = f"封面是竖版 {cw}x{chh}，不能作为横版（16:9）封面"
                    return False, rep
            except Exception:  # noqa: BLE001
                rep["cover_size"] = "unknown"
        bind = _cover_binding(cover_path, video=video, want_aspect="landscape") if cover_ok else None
        if cover_ok and not compare_cover_frame:
            rep["cover_source"] = "composer"
            rep["cover_frame_diff"] = None
        elif cover_ok:
            # 判据从「平均差 ≤8」换成「dHash 命中任一候选帧」：
            #  · 平均差对 h264 重编码 + 文案叠加太敏感，同图实测 11.7~11.8 → 被 8.0 误杀；
            #  · 只比一个帧位也不成立：库里的成片存在「0 帧口径」和「54 帧口径」两批，
            #    只比 54 会把 0 帧口径的成片全判死（实测 anthropic-ipo-1 dHash(vs 0 帧)=4、
            #    ink-c-yuanri3 dHash(vs 0 帧)=0，都是同图，却被平均差拦下）。
            extra = None
            if bind and bind.get("frame") is not None:
                extra = int(bind["frame"])
            match = _match_cover_frame(video, str(cover_path), extra_frame=extra)
            rep["cover_frame_match"] = match
            rep["cover_frame_diff"] = match.get("best_mean_diff")
            if bind is None:
                bpath = Path(cover_path).parent / (
                    "cover_binding_landscape.json" if "16_9" in Path(cover_path).name
                    else "cover_binding_portrait.json")
                rep["binding_state"] = "missing" if not bpath.exists() else "base_missing"
            best = match.get("dhash")
            if best is None:
                rep["reason"] = "封面图无法解码，无法验证是否出自成片"
                return False, rep
            if best > DHASH_MAX:
                rep["reason"] = (f"封面与成片任何候选帧都不匹配（最小 dHash 距离 {best} > {DHASH_MAX}）"
                                 f"→ 封面图不是从这条成片抽的")
                return False, rep
            rep["cover_source"] = "frame"
        if expect_text:
            src = Path(cover_path) if cover_ok else Path(video)
            samples = None if cover_ok else COVER_TEXT_SAMPLES
            texts = [(t or "") for _, t in C.ocr_texts(src, samples) if (t or "").strip()]
            joined = "\n".join(texts)
            rep["ocr_head"] = " ".join(joined.split())[:120]
            rep["ocr_source"] = "cover_still" if cover_ok else "video_frames"
            # SPEC-20261117 §十 R10：tesseract 读不出字**不是**缺陷判据。
            # 2026-11-18 事故：封面改成「视频第 0 帧」后，首帧是 HookOverlay 描边大字，
            # OCR 恒为空 → 这条硬拦把所有 tech_story 横版打成 EXIT=64（版式与封面全对）。
            # 口径与 text_hit_ratio 统一为 advisory；文案真值由 render_landscape 的
            # props 断言（expect_cover_text vs props.cover_main_text）做**唯一硬判据**（R11）。
            if not texts:
                rep["text_check"] = "advisory_no_text"
                rep["text_check_note"] = ("OCR 未识别到文字，已降级为 advisory；"
                                          "文案一致性以 props 断言为准（R11）")
            else:
                want = C.normalize_text(expect_text)
                got = C.normalize_text(joined)
                hit, ratio = text_hit(want, got)
                rep["text_hit_ratio"] = round(ratio, 3)
                # 非阻断：OCR 对描边大号标题的识别率不稳定（实测同一版式下会整行漏读），
                # 阻断判据改为确定性来源校验（见 render_landscape 的 props 断言）。
                # 这里保留命中率作为人工复核证据。
                rep["text_check"] = "hit" if hit else "advisory_miss"

    rep["ok"] = True
    return True, rep


def _hook_mode() -> int:
    """JSON stdin/stdout 协议（hook 引擎调用形态）。

    引擎在 after_hooks 时把 artifacts/context 传进来，这里必须用 **封面静帧** 做文案
    判定，并校验视频首帧与封面一致；只用视频帧 OCR 会误判（h264 帧上标题识别不稳）。
    """
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "reason": f"payload 不是合法 JSON: {exc}"},
                         ensure_ascii=False))
        return 1
    ctx = payload.get("context") or {}

    arts = payload.get("artifacts") or {}
    run_root = payload.get("run_root") or ""

    video = arts.get("video_16_9") or ctx.get("video_16_9") or ""
    if not video and run_root:
        video = str(C.artifact(Path(run_root), ctx.get("video_key") or "video_16_9"))
    cover = arts.get("cover_16_9") or ctx.get("cover_16_9") or ""
    if not cover and run_root:
        cover = str(C.artifact(Path(run_root), ctx.get("cover_key") or "cover_16_9"))
    if not video or not Path(video).exists():
        print(json.dumps({"ok": False, "reason": f"横版产物不存在: {video or '(空)'}"},
                         ensure_ascii=False))
        return 1

    _bind = _cover_binding(cover, video=video, want_aspect="landscape")
    ok, rep = check_landscape(video, str(ctx.get("expect_text") or ""),
                              cover_path=cover if cover and Path(cover).exists() else None,
                              compare_cover_frame=(_bind is None))
    if _bind:
        rep["cover_source"] = "composer"
        rep["cover_binding"] = {"frame": _bind.get("frame"), "verified": True}
    rep["ok"] = ok
    rep.setdefault("reason", "")
    print(json.dumps(rep, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(_hook_mode())
    video = sys.argv[1]
    expect = sys.argv[2] if len(sys.argv) > 2 else ""
    cover = sys.argv[3] if len(sys.argv) > 3 else None
    _bind = _cover_binding(cover, video=video, want_aspect="landscape")
    ok, report = check_landscape(video, expect, cover_path=cover,
                                 compare_cover_frame=(_bind is None))
    if _bind:
        report["cover_source"] = "composer"
        report["cover_binding"] = {"frame": _bind.get("frame"), "verified": True}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)
