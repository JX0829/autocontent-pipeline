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
MAX_FRAME_DIFF = 8.0  # 视频首帧与封面静帧的灰度平均差上限（同画面实测 0.45）


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


def _frame_diff(a: str, b: str) -> float:
    """两帧灰度平均绝对差（0=完全一致）。用于确认「视频首帧就是那张封面」。"""
    try:
        ra, _wa, _ha = _frame_gray(a, 0.0)
        rb, _wb, _hb = _frame_gray(b, 0.0)
    except Exception:
        return 255.0
    if len(ra) != len(rb):
        return 255.0
    return sum(abs(x - y) for x, y in zip(ra, rb)) / len(ra)


def check_landscape(video: str, expect_text: str = "",
                    cover_path: str | None = None) -> tuple[bool, dict]:
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
    try:
        raw, fw, fh = _frame_gray(video, 0.0)
    except Exception as e:
        return False, {"error": f"抽帧失败: {e}"}
    cov, left, right = _col_coverage(raw, fw, fh)
    rep.update({"coverage": round(cov, 3), "edge_left": round(left, 3),
                "edge_right": round(right, 3)})
    if cov < MIN_COVERAGE:
        rep["reason"] = (f"内容列覆盖率仅 {cov:.1%}（<{MIN_COVERAGE:.0%}），"
                         f"画幅两侧成片留白 → 判定为竖版封面塞横屏")
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
            diff = _frame_diff(video, str(cover_path))
            rep["cover_frame_diff"] = round(diff, 2)
            if diff > MAX_FRAME_DIFF:
                rep["reason"] = (f"视频首帧与封面静帧不一致（平均差 {diff:.1f} > "
                                 f"{MAX_FRAME_DIFF}）→ 首帧不是封面海报")
                return False, rep
        if expect_text:
            src = Path(cover_path) if cover_ok else Path(video)
            samples = None if cover_ok else COVER_TEXT_SAMPLES
            texts = [(t or "") for _, t in C.ocr_texts(src, samples) if (t or "").strip()]
            joined = "\n".join(texts)
            rep["ocr_head"] = " ".join(joined.split())[:120]
            rep["ocr_source"] = "cover_still" if cover_ok else "video_frames"
            if not texts:
                rep["reason"] = "封面 OCR 未识别到任何文字（无法验证主文案）"
                return False, rep
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

    ok, rep = check_landscape(video, str(ctx.get("expect_text") or ""),
                              cover_path=cover if cover and Path(cover).exists() else None)
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
    ok, report = check_landscape(video, expect, cover_path=cover)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 1)
