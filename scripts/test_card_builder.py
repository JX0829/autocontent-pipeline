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
    btns: list[dict] = []
    collect_buttons(card, btns)
    check(f"{label}: 两个按钮（确认/不发布）", len(btns) == 2, str(len(btns)))
    decisions = set()
    for b in btns:
        beh = (b.get("behaviors") or [{}])[0]
        check(f"{label}: 按钮 behaviors=callback ({b.get('text', {}).get('content')})",
              beh.get("type") == "callback")
        val = beh.get("value") or {}
        decisions.add(val.get("decision"))
        check(f"{label}: 按钮 value 带 run_id ({val.get('decision')})", bool(val.get("run_id")))
    check(f"{label}: 动作为 approve + reject", decisions == {"approve", "reject"}, str(decisions))


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


def test_publish_receipt() -> None:
    """发布回执卡：≤100 字、单一实现、错误码中文化（2026-11-11 收敛）。"""
    print("\n[发布回执卡]")
    from src.feishu.card_builder import FeishuCardBuilder, publish_receipt_text

    cases = [
        ("三平台全成功",
         {"published": [{"platform": "douyin"}, {"platform": "bilibili"},
                        {"platform": "channels"}], "failed": [], "skipped": []},
         "Anthropic冲刺纳斯达克", "✅ 已发布 3/3：抖音·B站·视频号"),
        ("单平台失败",
         {"published": [{"platform": "douyin"}, {"platform": "channels"}],
          "failed": [{"platform": "bilibili", "error": "LOGIN_EXPIRED: 登录态过期"}], "skipped": []},
         "Claude双版本炸场", "B站失败：登录过期"),
        ("全挂", {"published": [], "failed": [
            {"platform": "douyin", "error": "PUBLISH_SIGNAL_MISSING"},
            {"platform": "bilibili", "error": "CDP_UNAVAILABLE"},
            {"platform": "channels", "error": "LOGIN_EXPIRED"}], "skipped": []},
         "", "0/3 成功"),
        ("门禁拦下", {"published": [], "failed": [], "skipped": [{"gate": "enabled"}]},
         "Qwen4开源", "⏭ 未发布"),
        ("空结果", {"published": [], "failed": [], "skipped": []}, "", "无平台结果"),
        ("超长标题", {"published": [{"platform": "douyin"}], "failed": [], "skipped": []},
         "OpenAI发布GPT-5.5turbo并宣布全面免费开放给所有开发者", "…"),
    ]
    for name, report, title, expect in cases:
        text = publish_receipt_text(report, title)
        check(f"{name}: 摘要 ≤100 字（{len(text)}）", len(text) <= 100, text)
        check(f"{name}: 含预期关键词", expect in text, text)

    # 英文错误码不许被截半（历史坑：PUBLISH_SIGNAL_MISSI）
    t = publish_receipt_text({"published": [], "failed": [
        {"platform": "bilibili", "error": "PUBLISH_SIGNAL_MISSING"}], "skipped": []})
    check("错误码中文化，不留半截英文", "PUBLISH_SIGNAL" not in t and "未确认发布" in t, t)

    card = FeishuCardBuilder.build_publish_receipt(
        {"published": [{"platform": "douyin"}], "failed": []}, "标题A")
    check("回执卡是 Card 2.0", card.get("schema") == CARD2_SCHEMA, str(card.get("schema")))
    check("回执卡表头固定「发布回执」",
          (card.get("header") or {}).get("title", {}).get("content") == "发布回执")
    check("成功用绿色、失败用橙色",
          card.get("header", {}).get("template") == "green"
          and FeishuCardBuilder.build_publish_receipt(
              {"published": [], "failed": [{"platform": "douyin", "error": "x"}]},
              "").get("header", {}).get("template") == "orange")
    tags: list[str] = []
    walk_tags(card, tags)
    check("回执卡无 Card 1.0 遗留标签", not tags, str(tags))

    # 唯一实现：CLI 不许再自己拼回执/文案
    src = (PROJ / "scripts/publish_channels.py").read_text(encoding="utf-8")
    check("publish_channels 不再自带 compose_caption_body",
          "def compose_caption_body" not in src)
    check("publish_channels 走 pipeline 的发布实现", "_invoke_provider" in src)
    check("publish_channels 复用同一张回执卡", "send_publish_receipt" in src)


def main() -> int:
    print("=== 审批卡片 Card 2.0 回归（权威来源 src/approval/styles.py）===")
    test_v2()
    test_minimal()
    test_style_registry()
    test_publish_receipt()
    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("CARD2 CONTRACT FAIL")
        return 1
    print("CARD2 CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
