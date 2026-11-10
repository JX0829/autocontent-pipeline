#!/usr/bin/env python3
"""审批卡片 Card 2.0 结构回归（挂 verify_all.sh --fast）

背景：2026-11-10 前 `build_approval_card` 输出的是 Card 1.0（div/lark_md/action/note），
移动端窄栏、按钮回调丢失。本测试锁定 2.0 契约，防止有人改回去。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.feishu.card_builder import CARD2_SCHEMA, FeishuCardBuilder  # noqa: E402

# 1.0 特有标签：一旦出现说明又退回旧格式
LEGACY_TAGS = {"div", "action", "note", "lark_md"}
# 2.0 合法标签：plain_text 仅用于按钮文字/标题，不算遗留
ALLOWED_BUTTON_ACTIONS = {"APPROVE_AND_DISPATCH", "REJECT_TASK", "COMPLETE_MANUAL_PUBLISH"}

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


def walk_tags(node, out: list[str], path: str = "root") -> None:
    if isinstance(node, dict):
        if node.get("tag") in LEGACY_TAGS:
            out.append(f"{path}.{node.get('tag')}")
        for k, v in node.items():
            walk_tags(v, out, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk_tags(v, out, f"{path}[{i}]")


def collect_buttons(node, out: list[dict]) -> None:
    if isinstance(node, dict):
        if node.get("tag") == "button":
            out.append(node)
        for v in node.values():
            collect_buttons(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_buttons(v, out)


def base_kwargs(**over) -> dict:
    kw = dict(
        task_id="digest-2-matt-skills",
        title="Claude Code先问需求再写码",
        topics=["Claude Code", "Coding Agent", "开源项目"],
        content_text="Matt Pock 开源 Skills，为 Claude Code 拆分 Coding Agent 流程。",
        resource_keyword="skills",
        fact_summary="GitHub 开源 Skills 仓库。",
        duration_sec=26.8,
        video_size_mb=3.2,
        template_id="linear_obsidian",
    )
    kw.update(over)
    return kw


def test_common(card: dict, status: str) -> None:
    print(f"\n[{status}]")
    check("schema 是 2.0", card.get("schema") == CARD2_SCHEMA, str(card.get("schema")))
    check("顶层键为 schema/config/header/body",
          set(card) == {"schema", "config", "header", "body"}, str(list(card)))
    check("有 body.elements", bool((card.get("body") or {}).get("elements")))

    legacy: list[str] = []
    walk_tags(card, legacy)
    check("无 1.0 遗留元素(div/action/note/lark_md)", not legacy, str(legacy[:3]))

    check("header 有标题与模板色",
          bool((card.get("header") or {}).get("title")) and
          bool((card.get("header") or {}).get("template")))
    check("config.update_multi 开启", (card.get("config") or {}).get("update_multi") is True)

    try:
        json.dumps(card)
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        print("    JSON 序列化失败:", e)
    check("整体可 JSON 序列化", ok)


def test_pending() -> None:
    card = FeishuCardBuilder.build_approval_card(**base_kwargs(status="PENDING_APPROVAL"))
    test_common(card, "PENDING_APPROVAL")
    btns: list[dict] = []
    collect_buttons(card, btns)
    check("两个按钮", len(btns) == 2, str(len(btns)))
    acts = []
    for b in btns:
        beh = (b.get("behaviors") or [{}])[0]
        check_b = beh.get("type") == "callback"
        acts.append((beh.get("value") or {}).get("action"))
        check(f"按钮 behaviors 为 callback ({b.get('text', {}).get('content')})", check_b)
        check("按钮 value 带 task_id",
              (beh.get("value") or {}).get("task_id") == base_kwargs()["task_id"])
    check("按钮动作符合契约", set(acts) <= ALLOWED_BUTTON_ACTIONS and set(acts) == {
        "APPROVE_AND_DISPATCH", "REJECT_TASK"}, str(acts))


def test_rejected() -> None:
    card = FeishuCardBuilder.build_approval_card(
        **base_kwargs(status="REJECTED", rejection_reason="画面有错别字"))
    test_common(card, "REJECTED")
    blob = json.dumps(card, ensure_ascii=False)
    btns: list[dict] = []
    collect_buttons(card, btns)
    check("驳回态无操作按钮", len(btns) == 0, str(len(btns)))
    check("驳回原因进卡片", "画面有错别字" in blob)


def test_manual_publishing() -> None:
    card = FeishuCardBuilder.build_approval_card(**base_kwargs(status="MANUAL_PUBLISHING"))
    test_common(card, "MANUAL_PUBLISHING")
    btns: list[dict] = []
    collect_buttons(card, btns)
    check("单按钮", len(btns) == 1, str(len(btns)))
    if btns:
        beh = (btns[0].get("behaviors") or [{}])[0]
        check("动作为 COMPLETE_MANUAL_PUBLISH",
              (beh.get("value") or {}).get("action") == "COMPLETE_MANUAL_PUBLISH")


def test_approved() -> None:
    card = FeishuCardBuilder.build_approval_card(**base_kwargs(status="APPROVED"))
    test_common(card, "APPROVED")
    btns: list[dict] = []
    collect_buttons(card, btns)
    check("通过态无操作按钮", len(btns) == 0, str(len(btns)))
    check("模板色为 green", (card.get("header") or {}).get("template") == "green")


def main() -> int:
    print("=== 审批卡片 Card 2.0 回归 ===")
    test_pending()
    test_rejected()
    test_manual_publishing()
    test_approved()
    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("CARD2 CONTRACT FAIL")
        return 1
    print("CARD2 CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
