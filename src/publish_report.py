# -*- coding: utf-8 -*-
"""发布结果汇总 —— 「一个 run 只有一份结论、一张卡」的唯一实现。

背景（2026-11-15）：
    三平台原本可能分三次发：流水线发一批，之后又用单平台 CLI 补发视频号。
    流水线那次写 publish.json 并更新审批卡（1/1 B站），CLI 那次却**另发一张回执**
    （1/1 视频号），publish.json 还是老样子。于是同一个 run 有两张卡、各说一半，
    台账/明细/卡片三处对不上。

本模块把「合并 + 落盘 + 唯一卡片」收敛成一处：
    1. `merge_results()`  按平台合并新旧结果（同平台新结果覆盖旧结果）
    2. `write_report()`   重写 publish.json 与 publish_detail.md
    3. `finalize()`       有审批卡就**原地更新**成 N/N；没有审批卡才发一张独立回执
    4. `merge_and_finalize()` 上面三步合一，pipeline 与各单平台 CLI 共用
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from src.feishu.card_builder import publish_receipt_text

_BUCKETS = ("published", "failed", "skipped")


# --------------------------------------------------------------------------- 合并
def _platform_of(item: dict[str, Any]) -> str:
    return str(item.get("platform") or "").lower()


def merge_results(existing: dict[str, Any] | None, new_items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """把新结果按平台合并进已有报告：同平台新结果覆盖旧的（跨三个桶一起清）。"""
    report: dict[str, Any] = {"mode": "publish"}
    for b in _BUCKETS:
        report[b] = list((existing or {}).get(b) or [])

    replace = {_platform_of(i) for i in new_items}
    for b in _BUCKETS:
        report[b] = [x for x in report[b] if _platform_of(x) not in replace]

    for item in new_items:
        plat = _platform_of(item)
        if not plat:
            continue
        bucket = _bucket_of(item)
        report[bucket].append(item)
    return report


def _bucket_of(item: dict[str, Any]) -> str:
    """判定归属桶：显式 bucket > 门禁跳过 > 有 error > 已发布。"""
    b = str(item.get("bucket") or "")
    if b in _BUCKETS:
        return b
    if item.get("gate"):
        return "skipped"
    if item.get("error"):
        return "failed"
    return "published"


# --------------------------------------------------------------------------- 落盘
def write_report(run_root: Path, run_id: str, report: dict[str, Any], title: str = "") -> dict[str, Any]:
    """写 publish.json / publish_detail.md，并把 ≤100 字摘要挂到 report['receipt']。"""
    run_root = Path(run_root)
    report["receipt"] = publish_receipt_text(report, title)
    (run_root / "publish.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    detail = [f"# 发布明细 · {title or run_id}", "",
              f"- run_id: `{run_id}`", f"- 摘要: {report['receipt']}", "",
              "| 平台 | 结果 | 外部 ID | 说明 |", "|---|---|---|---|"]
    for it in report["published"]:
        detail.append(f"| {it.get('platform')} | ✅ 已发布 | `{it.get('msg_id') or it.get('post_id') or ''}` "
                      f"| {it.get('status') or ''} |")
    for it in report["failed"]:
        detail.append(f"| {it.get('platform')} | ❌ 失败 | — | {it.get('error') or it.get('message') or ''} |")
    for it in report["skipped"]:
        detail.append(f"| {it.get('platform') or '—'} | ⏭ 跳过 | — | {it.get('reason') or ''} |")
    (run_root / "publish_detail.md").write_text("\n".join(detail) + "\n", encoding="utf-8")
    return report


# --------------------------------------------------------------------------- 卡片
def _status_head(report: dict[str, Any], title: str) -> tuple[str, str]:
    ok, bad = len(report["published"]), len(report["failed"])
    total = ok + bad
    if bad:
        return f"⚠️ {ok}/{total} 成功 · {title}", "orange"
    if ok:
        return f"✅ 已发布 {ok}/{total} · {title}", "green"
    return f"⏭ 未发布 · {title}", "grey"


def _status_lines(report: dict[str, Any]) -> list[str]:
    lines = [report.get("receipt") or publish_receipt_text(report, "")]
    for r in report["published"]:
        lines.append(f"- ✅ {r.get('platform')} · `{r.get('msg_id') or r.get('post_id') or ''}`")
    for r in report["failed"]:
        lines.append(f"- ❌ {r.get('platform')}：{str(r.get('error') or r.get('message') or '')[:80]}")
    for r in report["skipped"]:
        lines.append(f"- ⏭ {r.get('platform')}：{str(r.get('reason') or '')[:60]}")
    return lines


def send_receipt_card(report: dict[str, Any], title: str = "") -> str:
    """发一张独立回执卡（仅用于没有 run 目录、没有审批卡的纯 CLI 场景）。"""
    if not (report.get("published") or report.get("failed")):
        return ""
    try:
        from src.feishu.approval_service import FeishuApprovalService
        from src.feishu.card_builder import FeishuCardBuilder
        chat = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
        if not chat:
            print("  ! 发布回执跳过：未配置 FEISHU_APPROVAL_CHAT_ID")
            return ""
        card = FeishuCardBuilder.build_publish_receipt(report, title)
        msg_id = FeishuApprovalService().send_approval_card(
            receive_id=chat,
            receive_id_type="chat_id" if chat.startswith("oc_") else "open_id",
            card_content=card)
        print(f"  ✓ 发布回执已发（msg_id={msg_id}）：{report.get('receipt')}")
        return f"sent:{msg_id}"
    except Exception as exc:  # noqa: BLE001
        print(f"  ! 发布回执发送失败（不影响发布结论）：{type(exc).__name__}: {exc}")
        return ""


def finalize_cards(run_root: Path, report: dict[str, Any], title: str = "") -> str:
    """一个 run 只留一张卡：有审批卡就原地更新，没有才发独立回执。

    返回 "updated:<run_id>" / "sent:<msg_id>" / ""。
    """
    run_root = Path(run_root)
    run_id = run_root.name
    has_card = (run_root / "approval_card.json").exists()

    if has_card:
        try:
            from src.approval.feishu import update_card_status
            head, tpl = _status_head(report, title)
            ok = update_card_status(run_id, head, tpl, _status_lines(report))
            if ok:
                return f"updated:{run_id}"
        except Exception as exc:  # noqa: BLE001 — 卡片失败不能改变发布结论
            print(f"  ! 审批卡原地更新失败（不影响发布结论）：{type(exc).__name__}: {exc}")

    return send_receipt_card(report, title)


# --------------------------------------------------------------------------- 一站式
def merge_and_finalize(run_root: Path, items: Iterable[dict[str, Any]],
                       title: str = "", update_card: bool = True) -> dict[str, Any]:
    """合并本次结果 → 重写 publish.json/明细 → 更新那张唯一的卡。

    `update_card=False`：只落文件、不碰卡片。发布预检（dry_run）用 —— 预检没发任何东西，
    却把审批卡改成「✅ 已发布 N/N」是骗人的（2026-11-15 踩过）。
    """
    run_root = Path(run_root)
    items = list(items)
    existing: dict[str, Any] = {}
    pj = run_root / "publish.json"
    if pj.exists():
        try:
            existing = json.loads(pj.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"  ! 旧 publish.json 损坏，按空报告重写：{exc}")
    report = merge_results(existing, items)
    write_report(run_root, run_root.name, report, title)
    if update_card:
        finalize_cards(run_root, report, title)
    return report
