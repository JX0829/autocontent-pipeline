#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.external_truth（gate, block）—— 外部真值校验（T2.6）

"成功"必须以**外部可验证的事实**为准，不能以函数返回值/日志为准。
按 `context.artifact_kind` 分派：

  vertical    ffprobe        → 必须 1080x1920
  landscape   ffprobe + OCR  → 必须 1920x1080，且封面文字 bbox 横向跨度 ≥ 75%
                               （QA#7：横版封面其实是竖版裁切图）
                               **多点采样**：默认取 0.1/0.2/0.3/0.4s 四点，
                               用中位数判定 + 有效采样率门槛，避免单帧噪声误判
  cover_text  tesseract OCR  → OCR 文本与 draft 的 title/resource_keyword
                               有 ≥1 个公共 token（防硬编码假文案）
                               **多点采样取并集**，单帧漏字不致误判
  sent        台账 + 飞书回查 → send_ledger 行存在且 feishu_msg_id 非空；
                               配了 FEISHU 凭据时再调 API 回查消息真实存在

CLI（spec 验收形态）：
    python3 scripts/hooks/external_truth.py --kind landscape --video path.mp4 --dry-run
    python3 scripts/hooks/external_truth.py --kind landscape --video path.mp4 --samples 0.1,0.2,0.3,0.4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

sys.path.insert(0, str(C.PROJ))

EXPECT = {
    "vertical": (1080, 1920),
    "landscape": (1920, 1080),
}
MIN_TEXT_SPAN = 0.75
MIN_VALID_RATIO = 0.5   # 多点采样中至少一半必须 OCR 成功，否则判可靠性不足


def _tokens(text: str) -> set[str]:
    """抽取中英文 token（中文用二元组，英文用单词），用于封面文案比对。"""
    norm = C.normalize_text(text)
    toks = {norm[i:i + 2] for i in range(max(0, len(norm) - 1))}
    for w in re.findall(r"[a-z0-9]{2,}", (text or "").lower()):
        toks.add(w)
    return {t for t in toks if t.strip()}


def check_vertical(video: Path) -> dict:
    size = C.video_size(video)
    if not size:
        return {"ok": False, "reason": "ffprobe 读不出分辨率（文件损坏/非视频）"}
    w, h = size
    expect = EXPECT["vertical"]
    if (w, h) != expect:
        return {"ok": False, "reason": f"竖版分辨率错误 {w}x{h}，应为 {expect[0]}x{expect[1]}",
                "ffprobe": {"width": w, "height": h}}
    return {"ok": True, "ffprobe": {"width": w, "height": h}, "kind": "vertical"}


def check_landscape(video: Path, timestamps: tuple[float, ...] | None = None) -> dict:
    size = C.video_size(video)
    if not size:
        return {"ok": False, "reason": "ffprobe 读不出分辨率（文件损坏/非视频）"}
    w, h = size
    expect = EXPECT["landscape"]
    if (w, h) != expect:
        return {"ok": False, "reason": f"横版分辨率错误 {w}x{h}，应为 {expect[0]}x{expect[1]}",
                "ffprobe": {"width": w, "height": h}}

    # 多采样：封面定格段内取多点，用中位数判定（单点 OCR 会被单帧噪声带偏）
    samples = C.ocr_text_spans(video, timestamps)
    table = [{"ts": ts, "span": (round(s, 3) if s is not None else None)} for ts, s in samples]
    valid = [s for _, s in samples if s is not None]
    need = 1 if len(samples) == 1 else max(2, math.ceil(len(samples) * MIN_VALID_RATIO))
    base = {"ffprobe": {"width": w, "height": h}, "samples": table,
            "samples_valid": len(valid), "samples_need": need}

    if not valid:
        return {"ok": False, "reason": f"封面 OCR 全部失败（{len(samples)} 个采样点均无文字），不许当作通过",
                **base, "text_span": None}
    if len(valid) < need:
        return {"ok": False,
                "reason": f"有效采样不足 {len(valid)}/{len(samples)}（需 ≥{need}），OCR 可靠性不达标",
                **base, "text_span": None}

    span = statistics.median(valid)
    base.update({"text_span": round(span, 3), "span_min": round(min(valid), 3),
                 "span_max": round(max(valid), 3), "stat": "median"})
    if span < MIN_TEXT_SPAN:
        return {"ok": False,
                "reason": (f"封面文字横向跨度中位数仅 {span:.1%}（<{MIN_TEXT_SPAN:.0%}），"
                           f"判定为竖版塞横屏；各采样点 {[s for s in valid]}"),
                **base}
    return {"ok": True, **base, "kind": "landscape"}


def check_cover_text(image_or_video: Path, draft: dict, keyword: str = "",
                     timestamps: tuple[float, ...] | None = None) -> dict:
    """多采样取并集再比 token：单帧 OCR 漏字不会导致误判为假文案。"""
    samples = C.ocr_texts(image_or_video, timestamps)
    table = [{"ts": ts, "chars": len((t or "").strip())} for ts, t in samples]
    texts = [(t or "") for _, t in samples if (t or "").strip()]
    if not texts:
        return {"ok": False, "reason": f"封面 OCR 未识别到任何文字（{len(samples)} 个采样点均为空）",
                "samples": table}
    txt = "\n".join(texts)

    want = _tokens(f"{draft.get('title') or ''} {keyword or ''}")
    got = _tokens(txt)
    common = want & got
    evidence = {"samples": table, "union_chars": len(txt.strip()),
                "ocr_head": " ".join(txt.split())[:80]}
    if not common:
        return {"ok": False,
                "reason": f"封面文案与 draft 标题/关键词无公共 token（OCR 前 40 字：{txt.strip()[:40]}）",
                **evidence}
    return {"ok": True, "common_tokens": sorted(common)[:8], **evidence, "kind": "cover_text"}


def check_sent(task_id: str, db_file: Path, chat_id: str | None, msg_id: str | None) -> dict:
    """发布真值：本地台账必须有确认过的 feishu_msg_id；有凭据再回查 API。"""
    if not db_file.exists():
        return {"ok": False, "reason": f"send_ledger 库不存在: {db_file}"}
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            "select dedupe_key, chat_id, feishu_msg_id from send_ledger where task_id = ?",
            (task_id,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"send_ledger 查询失败: {exc}"}

    if not rows:
        return {"ok": False, "reason": f"send_ledger 无 task={task_id} 的发送记录（seen 提前写？）"}
    confirmed = [r for r in rows if (r[2] or "").strip()]
    if not confirmed:
        return {"ok": False, "reason": "send_ledger 有行但 feishu_msg_id 为空：消息未经确认",
                "rows": len(rows)}
    if msg_id and not any(r[2] == msg_id for r in confirmed):
        return {"ok": False, "reason": f"台账里的 msg_id 与声称的 {msg_id} 不一致"}
    if chat_id and not any(r[1] == chat_id for r in confirmed):
        return {"ok": False, "reason": f"台账里的 chat_id 与声称的 {chat_id} 不一致"}

    api_checked = False
    if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"):
        try:  # 有凭据就真回查一次（失败按未验证处理，不放行也不谎报）
            import lark_oapi as lark  # type: ignore
            cli = lark.Client.builder().app_id(os.environ["FEISHU_APP_ID"]) \
                .app_secret(os.environ["FEISHU_APP_SECRET"]).build()
            req = lark.api.im.v1.GetMessageRequest.builder() \
                .message_id(confirmed[-1][2]).build()
            resp = cli.im.v1.message.get(req)
            if not getattr(resp, "success", lambda: False)():
                return {"ok": False, "reason": f"飞书回查失败: code={getattr(resp, 'code', '?')}"}
            api_checked = True
        except Exception as exc:
            return {"ok": False, "reason": f"飞书回查异常（不当作已发送）: {type(exc).__name__}: {exc}"}

    return {"ok": True, "message_id": confirmed[-1][2], "api_checked": api_checked,
            "ledger_rows": len(rows), "kind": "sent"}


def main() -> None:
    ap = argparse.ArgumentParser(description="外部真值校验（hook + CLI）")
    ap.add_argument("--kind", default=None, help="vertical | landscape | cover_text | sent")
    ap.add_argument("--video", default=None, help="视频/图片路径")
    ap.add_argument("--dry-run", action="store_true", help="CLI 直接跑，不读 stdin")
    ap.add_argument("--task-id", default="", help="CLI 模式：sent 校验用的 task_id")
    ap.add_argument("--expect-title", default="", help="CLI 模式：cover_text 校验用的期望标题")
    ap.add_argument("--samples", default=None,
                    help='采样时间点（秒），逗号分隔，默认 "0.1,0.2,0.3,0.4"')
    args, _ = ap.parse_known_args()
    ts_list, ts_bad = C.parse_ts_list_strict(args.samples)
    if ts_bad:
        print(json.dumps({"ok": False,
                          "reason": f"非法采样点 {ts_bad}（须为 0~3600 秒的数字，禁止静默丢弃）"},
                         ensure_ascii=False))
        raise SystemExit(2)

    # CLI 模式（spec 验收形态）
    if args.dry_run and args.kind:
        path = Path(args.video or "").expanduser()
        if args.kind in ("vertical", "landscape"):
            if not path.is_file():
                print(json.dumps({"ok": False, "reason": f"文件不存在: {path}"}, ensure_ascii=False))
                raise SystemExit(1)
            rep = check_vertical(path) if args.kind == "vertical" else check_landscape(path, ts_list)
        elif args.kind == "cover_text":
            rep = check_cover_text(path, {"title": args.expect_title}, "", ts_list)
        else:
            rep = check_sent(args.task_id, C.db_path({}), None, None)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        raise SystemExit(0 if rep.get("ok") else 1)

    # hook 协议模式
    p = C.load_payload()
    c = C.ctx(p)
    kind = str(c.get("artifact_kind") or "")
    if not kind:
        C.fail("external_truth: 缺 context.artifact_kind（vertical/landscape/cover_text/sent）")

    if kind == "sent":
        rep = check_sent(str(p.get("task_id") or ""), C.db_path(p),
                         c.get("chat_id"), c.get("feishu_msg_id"))
    else:
        # 按 kind 依次尝试合理的 artifact key，避免"明明有产物却报找不到"
        candidates = {
            "vertical": ("video_key", "vertical", "video", "video_9_16"),
            "landscape": ("video_key", "video_16_9", "landscape", "video"),
            "cover_text": ("cover_key", "cover", "cover_16_9", "cover_source"),
        }.get(kind, ("video_key", "video"))
        target = None
        for k in candidates:
            key = str(c.get(k) or "") if k.endswith("_key") else k
            if not key:
                continue
            target = C.artifact(p, key)
            if target is not None and target.exists():
                break
            target = None
        if target is None:
            C.fail(f"external_truth: 找不到 {kind} 产物（尝试过 {list(candidates)}）")
        if target.stat().st_size <= 0:
            C.fail(f"external_truth: 产物为 0 字节 → {target}")
        ts_list, ts_bad = C.parse_ts_list_strict(c.get("samples"))
        if ts_bad:
            C.fail(f"external_truth: 非法采样点 {ts_bad}（须为 0~3600 秒数字，禁止静默丢弃）")
        if kind == "vertical":
            rep = check_vertical(target)
        elif kind == "landscape":
            # 单一实现原则（R4）：横版出厂判据只认 scripts/landscape_guard.py。
            # 本文件早期也有一份 OCR 跨度版，两份并存会给出互相矛盾的结论
            # （实测：同一成片 guard 通过、这里判 70.4% 失败）。
            from scripts.landscape_guard import check_landscape as _guard_check
            cover = str(c.get("cover_16_9") or "")
            if not cover:
                try:
                    cover = str(C.artifact(p, c.get("cover_key") or "cover_16_9"))
                except Exception:  # noqa: BLE001 — 封面缺失时退化为纯视频判定
                    cover = ""
            ok_guard, rep = _guard_check(
                str(target), str(c.get("expect_text") or ""),
                cover_path=cover if cover and Path(cover).exists() else None,
            )
            rep["ok"] = ok_guard
            rep.setdefault("reason", "")
        else:
            draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script")) or {}
            rep = check_cover_text(target, draft, str(c.get("resource_keyword") or ""), ts_list)

    if not rep.get("ok"):
        C.fail(str(rep.get("reason") or "external_truth 未通过"), detail=rep)
    C.emit(True, patch={"external_truth": rep})


if __name__ == "__main__":
    main()
