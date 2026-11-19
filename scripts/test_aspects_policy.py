#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""画幅策略契约（2026-11-19）：课文本只出竖版，科技风横竖都要。

用户口径原话：「只是出课文背后的故事只出竖版，科技风还是横竖都要，不要搞混了」。

判据：
  T1 pipeline.yaml 有 aspects_by_template，且 textbook_story = [vertical]
  T2 课文物料（带 textbook 字段）→ aspects 收敛成 [vertical]，render_landscape 被 SKIP
  T3 科技物料 → aspects 保持 [vertical, landscape]（不许被课文本规则误伤）
  T4 ink_story 模板同样只出竖版
  T5 水墨库 to_render_props(inject_mid_end=True) 会注入 mid/end（课文本靠它们播飞鸟/残墨）
  T6 科技库 inject_mid_end=False 时 mid/end 保持 None（历史黑场问题不回归）
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ← {detail}" if detail and not ok else ""))


def main() -> int:
    cfg = yaml.safe_load((PROJ / "pipeline.yaml").read_text(encoding="utf-8"))
    by_tpl = cfg.get("aspects_by_template") or {}
    check("T1 aspects_by_template 已声明", bool(by_tpl), str(by_tpl))
    check("T1 textbook_story 只有 vertical", by_tpl.get("textbook_story") == ["vertical"],
          str(by_tpl.get("textbook_story")))
    check("T4 ink_story 只有 vertical", by_tpl.get("ink_story") == ["vertical"],
          str(by_tpl.get("ink_story")))

    from src.pipeline.runner import _filter_aspects_by_template

    base = ["vertical", "landscape"]
    check("T2 课文物料 → [vertical]",
          _filter_aspects_by_template(cfg, base, {"textbook": {"poem_title": "元日"}}) == ["vertical"])
    check("T3 科技物料 → 横竖都保留",
          _filter_aspects_by_template(cfg, base, {"title": "DeepSeek 额度"}) == base,
          str(_filter_aspects_by_template(cfg, base, {"title": "DeepSeek 额度"})))
    check("T4 content_type=ink_story → [vertical]",
          _filter_aspects_by_template(cfg, base, {"content_type": "ink_story"}) == ["vertical"])

    from src.assets_manager import AssetManager

    try:
        sel = AssetManager().select(run_id="aspect-ink-check", library="ink_story")
        props = sel.to_render_props(inject_mid_end=True)
        a = props.get("assets") or {}
        check("T5 水墨库注入 mid/end", a.get("mid") is not None and a.get("end") is not None,
              f"mid={a.get('mid')} end={a.get('end')}")
        check("T5 mid 指向 ink/ 素材", str(a.get("mid") or "").startswith("ink/"), str(a.get("mid")))
    except Exception as exc:  # noqa: BLE001
        check("T5 水墨库注入 mid/end", False, f"{type(exc).__name__}: {exc}")
        check("T5 mid 指向 ink/ 素材", False, "上一步失败")

    try:
        sel_tech = AssetManager().select(run_id="aspect-tech-check", library="tech_story")
        pt = (sel_tech.to_render_props().get("assets") or {})
        check("T6 科技库 mid/end 仍为 None", pt.get("mid") is None and pt.get("end") is None,
              f"mid={pt.get('mid')} end={pt.get('end')}")
    except Exception as exc:  # noqa: BLE001
        check("T6 科技库 mid/end 仍为 None", False, f"{type(exc).__name__}: {exc}")

    print(f"\n汇总: {len(PASS)}/{len(PASS) + len(FAIL)} PASS")
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
