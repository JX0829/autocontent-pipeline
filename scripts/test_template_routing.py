#!/usr/bin/env python3
"""模板路由契约测试：确保 theme_mapping 真的生效，而不是被 round_robin 挡掉。

背景：`resolve_template_id()` 的优先级是
    CLI > payload > 账号 > (default_template=="round_robin" ? 轮换 : theme_mapping) > default_template
所以只要 `default_template` 留成 `round_robin`，theme_mapping 里写什么都不生效。
本次就是因为这个坑，配置改了但线上仍跑旧模板（实测 resolve 出来是 editorial_news）。

用法：$PY scripts/test_template_routing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.templates_manager import TemplateManager  # noqa: E402

PASS = FAIL = 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    ok = got == want
    print(f"  {'✅' if ok else '❌'} {name}: {got}" + ("" if ok else f"（期望 {want}）"))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    tm = TemplateManager()
    cfg = tm._config

    print("=== 配置前提 ===")
    dt = cfg.get("default_template")
    print(f"  default_template = {dt}")
    if dt == "round_robin":
        print("  ⚠️  round_robin 会让 theme_mapping 失效——如果这是有意的轮换模式，忽略下面的 theme 用例")

    print("\n=== theme → 模板 ===")
    expect = (cfg.get("theme_mapping") or {})
    for theme, want in expect.items():
        check(f"theme={theme}", tm.resolve_template_id(theme=theme, payload_template="auto"), want)

    print("\n=== 退役模板（2026-11-18 起 T1~T5 退休，旧物料必须被收敛）===")
    retired = sorted(tid for tid, meta in (cfg.get("templates") or {}).items()
                     if isinstance(meta, dict) and meta.get("retired"))
    for tid in retired:
        check(f"payload={tid} → 收敛到可用模板",
              tm.resolve_template_id(theme="breaking-news", payload_template=tid),
              "tech_story")
    check("退役模板已 enabled:false", str([t for t in retired if t in tm.list_enabled_templates()]), "[]")

    print("\n=== composition 映射 ===")
    from src.pipeline.executors import COMPOSITION_BY_TEMPLATE, DEFAULT_VERTICAL_COMPOSITION
    for tid in expect.values():
        want = COMPOSITION_BY_TEMPLATE.get(tid, DEFAULT_VERTICAL_COMPOSITION)
        check(f"{tid} → composition", want, COMPOSITION_BY_TEMPLATE.get(tid, DEFAULT_VERTICAL_COMPOSITION))
    # 旧 Geek 排版模板已下线：T1~T5 必须也有明确映射且不得指向 GeekShortVideo
    for tid in ("editorial_news", "cupertino_glass", "neo_brutalism", "notion_craft", "linear_obsidian"):
        comp = COMPOSITION_BY_TEMPLATE.get(tid, DEFAULT_VERTICAL_COMPOSITION)
        check(f"{tid} → 不再是 GeekShortVideo", str(comp != "GeekShortVideo"), "True")

    print(f"\n结论: {'PASS' if FAIL == 0 else 'FAIL'}（{PASS} 通过 / {FAIL} 失败）")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
