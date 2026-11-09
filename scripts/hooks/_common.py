#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/hooks/_common.py —— hook 公共工具（T2.1 协议）

协议（spec_enhancement.md §5 T2.1）：
    stdin  : {"task_id","run_root","step_id","artifacts":{...},"context":{...}}
    stdout : {"ok": bool, "patch"?: object, "reason"?: string}

约定：
  · hook **永远 exit 0**，失败用 `ok=false` 表达（引擎据 ok 决定 gate/transform 行为）
  · 缺文件/缺字段一律 `ok=false` + 可读 reason，绝不"猜一个值糊过去"
  · 需要耗时/外部命令的 hook 自己做超时保护（引擎默认 30s 硬超时）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

PROJ = Path(__file__).resolve().parent.parent.parent
_log = logging.getLogger("hooks")


# ------------------------------------------------------------------ 协议 IO

def load_payload() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _emit({"ok": False, "reason": f"payload_not_json: {exc}"})
        raise SystemExit(0)


def _emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def emit(ok: bool, patch: dict[str, Any] | None = None, reason: str = "", **extra: Any) -> NoReturn:
    out: dict[str, Any] = {"ok": bool(ok)}
    if patch:
        out["patch"] = patch
    if reason:
        out["reason"] = reason
    out.update(extra)
    _emit(out)
    raise SystemExit(0)


def fail(reason: str, **extra: Any) -> NoReturn:
    emit(False, None, reason, **extra)


# ------------------------------------------------------------------ 取值

def ctx(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("context") or {}


def arts(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("artifacts") or {}


def run_root(payload: dict[str, Any]) -> Path:
    return Path(payload.get("run_root") or PROJ / "output" / "_tmp")


def artifact(payload: dict[str, Any], key: str) -> Path | None:
    """解析 artifact 路径：支持绝对路径、相对 run_root、以及 {key: {path: ...}} 形态。"""
    v = arts(payload).get(key)
    if isinstance(v, dict):
        v = v.get("path") or v.get("file")
    if not v:
        return None
    p = Path(str(v))
    return p if p.is_absolute() else run_root(payload) / p


def read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def text_of(obj: Any) -> str:
    """把 draft/manifest 之类的结构压平成可扫描文本。"""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(text_of(v) for v in obj.values() if v is not None)
    if isinstance(obj, (list, tuple)):
        return "\n".join(text_of(v) for v in obj)
    return str(obj)


# ------------------------------------------------------------------ 哈希

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def normalize_text(s: str) -> str:
    """归一化文本 —— **委托给全仓唯一实现** `src.utils.normalize`（T2.4）。"""
    try:
        sys.path.insert(0, str(PROJ))
        from src.utils.normalize import normalize_text as _norm
        return _norm(s)
    except Exception:  # 兜底：保证 hook 不因导入问题整体失效
        s = re.sub(r"\[[^\]]{1,8}R?\]", "", s or "").lower()
        return "".join(c for c in s if c.isalnum() or "\u4e00" <= c <= "\u9fff")


# ------------------------------------------------------------------ 外部命令

def sh(cmd: list[str], timeout: float = 20.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"命令不存在: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"命令超时({timeout}s): {' '.join(cmd[:3])}"


def ffprobe_json(path: Path, timeout: float = 20.0) -> dict[str, Any] | None:
    rc, out, _ = sh(["ffprobe", "-v", "error", "-print_format", "json",
                     "-show_format", "-show_streams", str(path)], timeout)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def video_size(path: Path) -> tuple[int, int] | None:
    info = ffprobe_json(path)
    if not info:
        return None
    for s in info.get("streams") or []:
        if s.get("codec_type") == "video":
            return int(s.get("width") or 0), int(s.get("height") or 0)
    return None


def video_duration(path: Path) -> float | None:
    info = ffprobe_json(path)
    if not info:
        return None
    try:
        return float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return None


def frame_gray(path: Path, ts: float = 0.2, timeout: float = 25.0) -> tuple[bytes, int, int] | None:
    """抽一帧转灰度原始字节（**二进制**读取，不能走 text=True，否则会解码崩溃）。"""
    size = video_size(path)
    if not size:
        return None
    w, h = size
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(ts), "-i", str(path),
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    data = p.stdout or b""
    if p.returncode != 0 or len(data) < w * h:
        return None
    return data[: w * h], w, h


def frame_gray32(path: Path, ts: float = 0.5, timeout: float = 25.0) -> bytes | None:
    """抽一帧并缩放到 32x32 灰度（T2.4 视觉指纹：抗分辨率/码率差异）。"""
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(ts), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=32:32:flags=area",
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    data = p.stdout or b""
    if p.returncode != 0 or len(data) < 32 * 32:
        return None
    return data[: 32 * 32]


def ocr_text_span(path: Path, ts: float = 0.2, timeout: float = 60.0) -> float | None:
    """OCR 取文字 bbox 的横向跨度占画面宽度比例（用于横版封面错版判定）。

    返回 None = OCR 失败（无文字/引擎异常）。注意：临时文件用 tempfile（/var/folders），
    不要写死 `/tmp/xxx.png` —— QA#12 实测 macOS 上会读不到。
    """
    import csv
    import io
    import tempfile
    image_ext = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = Path(tf.name)
    try:
        if path.suffix.lower() in image_ext:
            # 静态图片：复制到真实临时路径再 OCR。
            # 不能直接用原路径 —— macOS 上 tesseract 打不开 /tmp/xxx（/tmp 是软链，QA#12）
            import shutil
            shutil.copyfile(path, png)
        else:
            rc, _, err = sh(["ffmpeg", "-y", "-ss", str(ts), "-i", str(path), "-vframes", "1",
                             "-loglevel", "error", str(png)], timeout)
            if rc != 0 or not png.exists():
                return None
        rc2, out, _ = sh(["tesseract", str(png), "stdout", "-l", "chi_sim+eng", "tsv"], timeout)
        if rc2 != 0 or not out.strip():
            return None
        rows = list(csv.DictReader(io.StringIO(out), delimiter="\t"))
        words = [r for r in rows if (r.get("text") or "").strip()]
        if not words:
            return None
        size = video_size(png)  # 图片同样可被 ffprobe 读
        if not size:
            return None
        width = size[0]
        lefts = [int(r["left"]) for r in words if str(r.get("left", "")).strip().isdigit()]
        rights = [int(r["left"]) + int(r["width"]) for r in words
                  if str(r.get("left", "")).strip().isdigit() and str(r.get("width", "")).strip().isdigit()]
        if not lefts:
            return None
        return (max(rights) - min(lefts)) / width
    except Exception as exc:  # noqa: BLE001
        _log.warning("ocr_text_span 失败 %s: %s", path.name, exc)
        return None
    finally:
        if png != path:
            png.unlink(missing_ok=True)


def ocr_text(path: Path, ts: float = 0.2, timeout: float = 60.0) -> str | None:
    """OCR 抽取文本（用于封面文案真值校验）。"""
    import tempfile
    image_ext = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
        png = Path(tf.name)
    try:
        if path.suffix.lower() in image_ext:
            import shutil
            shutil.copyfile(path, png)
        else:
            rc, _, _ = sh(["ffmpeg", "-y", "-ss", str(ts), "-i", str(path), "-vframes", "1",
                           "-loglevel", "error", str(png)], timeout)
            if rc != 0 or not png.exists():
                return None
        rc2, out, _ = sh(["tesseract", str(png), "stdout", "-l", "chi_sim+eng"], timeout)
        if rc2 != 0:
            _log.warning("tesseract 返回 %s（%s）", rc2, path.name)
            return None
        return out
    except Exception as exc:  # noqa: BLE001
        _log.warning("ocr_text 失败 %s: %s", path.name, exc)
        return None
    finally:
        if png != path:
            png.unlink(missing_ok=True)


# ------------------------------------------------------------ 多采样（T2.6）

# 封面定格段 [0, 0.5s] 内的采样点。单点 OCR 会被单帧噪声（转场、抗锯齿抖动、
# tesseract 单帧误识）带偏，官方错版判定必须取多点统计量。
# 采样窗口必须落在**版式稳定段**：0.1~0.4s 还在开场动画里（标题逐个入场）。
# 实测同一支横版成片：0.1~0.4s 跨度恒为 18.8%，1.0s 之后稳定在 89.8%。
# 用动画帧取中位数，会把正常横版封面判成「竖版塞横屏」。
DEFAULT_SAMPLE_TS: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def parse_ts_list(raw) -> tuple[float, ...]:
    """解析采样点：接受 `"0.1,0.2"` 字符串、[0.1, 0.2] 列表或 None；非法回退默认。

    注意：本函数**容错**（供宽松场景用）。校验场景请用 `parse_ts_list_strict`，
    否则非法采样点会被静默丢弃 → 采样数变少 → 门槛悄悄变松（静默降级）。
    """
    ts, _ = parse_ts_list_strict(raw)
    return ts or DEFAULT_SAMPLE_TS


def parse_ts_list_strict(raw) -> tuple[tuple[float, ...], list[str]]:
    """严格解析：返回 (合法采样点, 非法片段列表)。非法片段非空时调用方必须 fail。"""
    if raw is None or raw == "" or raw == []:
        return DEFAULT_SAMPLE_TS, []
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).replace(" ", "").split(",")
    out: list[float] = []
    bad: list[str] = []
    for part in parts:
        try:
            v = float(part)
        except (TypeError, ValueError):
            bad.append(str(part))
            continue
        if 0.0 <= v <= 3600.0:
            out.append(v)
        else:
            bad.append(str(part))
    return tuple(out), bad


def ocr_text_spans(path: Path, timestamps: tuple[float, ...] | None = None,
                   timeout: float = 60.0) -> list[tuple[float, float | None]]:
    """多点采样：返回 [(ts, span|None)]。静态图片只采一次。"""
    if is_image(path):
        return [(0.0, ocr_text_span(path, 0.0, timeout))]
    return [(ts, ocr_text_span(path, ts, timeout)) for ts in (timestamps or DEFAULT_SAMPLE_TS)]


def ocr_texts(path: Path, timestamps: tuple[float, ...] | None = None,
              timeout: float = 60.0) -> list[tuple[float, str | None]]:
    """多点采样：返回 [(ts, text|None)]。静态图片只采一次。"""
    if is_image(path):
        return [(0.0, ocr_text(path, 0.0, timeout))]
    return [(ts, ocr_text(path, ts, timeout)) for ts in (timestamps or DEFAULT_SAMPLE_TS)]


# ------------------------------------------------------------------ 数据面
def db_path(payload: dict[str, Any]) -> Path:
    override = ctx(payload).get("db_path")
    if override:
        return Path(override)
    return Path(os.getenv("SQLITE_DB_PATH") or PROJ / "data" / "pipeline_state.db")


def ledger_path(name: str) -> Path:
    """台账路径。`AUTOCONTENT_DATA_DIR` 可重定向（自检/CI 用，避免污染真实台账）。"""
    override = os.getenv("AUTOCONTENT_DATA_DIR")
    base = Path(override) if override else (PROJ / "data")
    return base / f"{name}.jsonl"


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: int = 5000) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            _log.warning("台账坏行已跳过 %s: %s", path.name, exc)
            continue
    return rows


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
