#!/usr/bin/env python3
"""审批卡片 Card 2.0 结构回归（挂 verify_all.sh --fast）

权威来源：`src/approval/styles.py`（卡片样式与审批 provider 绑定）。
历史：2026-11-10 之前审批卡是 Card 1.0（div/lark_md/action/note），移动端窄栏、按钮回调丢失；
同日出现过两份并行的审批卡构造器（旧的工作台卡 + 新的审批卡），已收敛到 styles.py，
本测试同时锁住「结构不许退回 1.0」和「pipeline.yaml 声明的样式必须真实存在」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.approval.styles import STYLES, get_style  # noqa: E402

CARD2_SCHEMA = "2.0"
LEGACY_TAGS = {"div", "action", "note", "lark_md"}

TARGETS = [
    {"provider": "douyin", "aspect": "vertical", "with_title": True, "with_description": True,
     "declare_aigc": True, "declare_original": False},
    {"provider": "bilibili", "aspect": "landscape", "with_title": True, "with_description": True,
     "declare_aigc": True, "declare_original": True},
]

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


def test_common(card: dict, label: str) -> None:
    print(f"\n[{label}]")
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


def test_buttons(card: dict, label: str) -> None:
    """按钮已改纯展示：卡片里不该再有 button / behaviors。

    原因：飞书卡片回调（card.action.trigger）未开通时，带 behaviors=callback 的按钮
    点下去只会弹错误码（200830）。所以裁决一律走群里回复 approve/reject，
    卡片上只保留字样，不渲染任何可点元素。
    """
    btns: list[dict] = []
    collect_buttons(card, btns)
    check(f"{label}: 无按钮元素（已改纯展示）", len(btns) == 0, str(len(btns)))
    raw = json.dumps(card, ensure_ascii=False)
    check(f"{label}: 卡片里无 behaviors 回调", '"behaviors"' not in raw)
    check(f"{label}: 文案给出回复指令",
          "approve" in raw and "reject" in raw)


def test_v2() -> None:
    card = get_style("approval_v2")(run_id="run-1", title="Claude双版本炸场",
                                    targets=TARGETS, header_template="blue",
                                    show_publish_matrix=True)
    test_common(card, "approval_v2")
    test_buttons(card, "approval_v2")
    blob = json.dumps(card, ensure_ascii=False)
    check("v2: 展示发布矩阵（抖音 + B站）", "douyin" in blob and "bilibili" in blob)
    check("v2: 带 AI 声明 / 原创标记", "AI声明" in blob and "原创" in blob)
    check("v2: 表头模板色为 blue", (card.get("header") or {}).get("template") == "blue")

    no_matrix = get_style("approval_v2")(run_id="run-1", title="t", targets=TARGETS,
                                         show_publish_matrix=False)
    check("v2: show_publish_matrix=False 时不出现平台名",
          "douyin" not in json.dumps(no_matrix, ensure_ascii=False))


def test_minimal() -> None:
    card = get_style("approval_minimal")(run_id="run-1", title="标题A", targets=TARGETS)
    test_common(card, "approval_minimal")
    test_buttons(card, "approval_minimal")
    blob = json.dumps(card, ensure_ascii=False)
    check("minimal: 不展示发布矩阵", "douyin" not in blob)
    check("minimal: 表头模板色为 grey", (card.get("header") or {}).get("template") == "grey")

    v2 = get_style("approval_v2")(run_id="run-1", title="标题A", targets=TARGETS)
    check("两种样式结构确实不同（绑定生效）",
          json.dumps(card, sort_keys=True) != json.dumps(v2, sort_keys=True))


def test_style_registry() -> None:
    print("\n[样式注册表]")
    check("未知样式名必须显式报错", _raises_keyerror("approval_does_not_exist"))
    check("STYLES 至少含 v2 / minimal", {"approval_v2", "approval_minimal"} <= set(STYLES))

    # pipeline.yaml 声明的 card_style 必须真实存在，否则审批一上线就降级
    import yaml

    cfg = yaml.safe_load((PROJ / "pipeline.yaml").read_text(encoding="utf-8")) or {}
    declared = [(slot, (p.get("profile") or {}).get("card_style"))
                for slot, p in (cfg.get("providers") or {}).items()
                if isinstance(p, dict) and p.get("kind") == "approval"
                and (p.get("profile") or {}).get("renders_card", True)]
    for slot, style in declared:
        check(f"pipeline.yaml {slot} 的 card_style 存在于 STYLES ({style})", style in STYLES)
    check("至少声明了一个会渲染卡片的审批 provider", bool(declared), str(declared))


def _raises_keyerror(name: str) -> bool:
    try:
        get_style(name)
    except KeyError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main() -> int:
    print("=== 审批卡片 Card 2.0 回归（权威来源 src/approval/styles.py）===")
    test_v2()
    test_minimal()
    test_style_registry()
    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("CARD2 CONTRACT FAIL")
        return 1
    print("CARD2 CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
