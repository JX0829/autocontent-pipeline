#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""素材轮换（随机）巡检 —— 防止「素材随机又变回固定一条」。

背景（2026-11-18 用户两次追问）：
  · 用户：「现在小品新出的视频，还是只用了那个芯片模板，根本就没有换模板」
  · 实测旧行为：bg 12/12 全是 `bg_circuit_v1_loop`、`mid/end` 恒 None（槽位没配），
    所以每条片子的画面骨架完全一样，看着像"没换模板"。

本脚本守四条：
  ① 同 run_id 幂等：同一物料重渲必须选到同一批素材（可复现、审批卡不撒谎）
  ② 每槽位覆盖：抽样 N 次，池里每个候选都要被抽到过（end 只有 1 条，跳过）
  ③ bg 不许锁死：抽样里 distinct bg family ≥ 3
  ④ Geek 下线：DEFAULT_VERTICAL_COMPOSITION 必须是 TechStoryVideo，T1~T5 不得再指向 Geek

跑法：python3 scripts/test_asset_rotation.py [--runs 48]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✅' if ok else '❌'} {name}{(' ' + detail) if detail else ''}")
    if not ok:
        fails.append(name)


def _draft() -> dict:
    """取一份真实 draft 当输入；没有就退化成最小可用结构。"""
    runs = sorted((PROJ / "output").glob("*/*/draft_script.json"))
    if runs:
        return json.loads(runs[-1].read_text(encoding="utf-8"))
    return {"title": "测试物料", "tags": ["科技"],
            "scenes": [{"segment_narration": "科技测试"}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=48)
    args = ap.parse_args()

    from src.assets_manager import AssetManager
    from src.pipeline.executors import DEFAULT_VERTICAL_COMPOSITION, COMPOSITION_BY_TEMPLATE

    mgr = AssetManager()
    draft = _draft()

    # ① 幂等
    a = mgr.select(run_id="rotation-idem", draft=draft)
    b = mgr.select(run_id="rotation-idem", draft=draft)
    same = all(getattr(a, s) and getattr(b, s) and getattr(a, s).id == getattr(b, s).id
               for s in ("bg", "mid", "end"))
    check("① 同 run_id 选材幂等", same,
          f"bg={a.bg.id} mid={getattr(a.mid, 'id', None)} end={getattr(a.end, 'id', None)}")

    # ② / ③ 抽样覆盖
    picks: dict[str, Counter] = {s: Counter() for s in ("open", "bg", "mid", "end", "bgm")}
    for i in range(args.runs):
        sel = mgr.select(run_id=f"rotation-{i}", draft=draft)
        for s in picks:
            c = getattr(sel, s, None)
            picks[s][c.id if c else "NONE"] += 1

    name, lib = mgr._library("tech_story")
    manifest = mgr._manifest(name, lib)
    pool = {s: {a["id"] for a in manifest["assets"] if a.get("slot") == s} for s in picks}

    # bg 的随机单位是「视觉族」（同 family 的 _loop/v1/v2 算一个，见 assets_manager 注释）
    families = {a.get("family") for a in manifest["assets"] if a.get("slot") == "bg"}
    fam_of = {a["id"]: a.get("family") for a in manifest["assets"] if a.get("slot") == "bg"}

    for s in ("open", "bg", "mid", "bgm"):
        if s == "bg":
            want = {f for f in families if f}
            got = {fam_of.get(i) for i in set(picks[s]) - {"NONE"}} - {None}
            ratio = len(got) / len(want) if want else 1.0
            check(f"② 槽位 bg 族覆盖 ≥80%（随机单位=family）", ratio >= 0.8,
                  f"{len(got)}/{len(want)} 族被抽到")
            continue
        want = pool[s]
        got = set(picks[s]) - {"NONE"}
        ratio = len(got) / len(want) if want else 1.0
        check(f"② 槽位 {s} 覆盖 ≥80%", ratio >= 0.8,
              f"{len(got)}/{len(want)} 条被抽到（抽样 {args.runs} 次）")
    # 2026-11-19 回滚：mid/end 素材开头是黑场 → 压场景交界会全黑闪断，已停止注入渲染。
    # 选材仍会记录（audit 可查），但 props 里必须是 None，否则又会闪黑。
    from src.assets_manager import AssetManager as _AM
    _sel = _AM().select(run_id="rollback-check", draft=draft)
    _rp = _sel.to_render_props()["assets"]
    check("② mid/end 已回滚（不再注入 props）",
          _rp.get("mid") is None and _rp.get("end") is None,
          f"mid={_rp.get('mid')} end={_rp.get('end')}")

    distinct = len(set(picks["bg"]) - {"NONE"})
    check("③ bg 未被锁死（distinct ≥3）", distinct >= 3, f"distinct={distinct} {dict(picks['bg'])}")

    # ④ Geek 下线
    check("④ 默认竖版模板 = TechStoryVideo", DEFAULT_VERTICAL_COMPOSITION == "TechStoryVideo",
          DEFAULT_VERTICAL_COMPOSITION)
    geek = [t for t, c in COMPOSITION_BY_TEMPLATE.items() if c == "GeekShortVideo"]
    check("④ 无模板再指向 GeekShortVideo", not geek, f"残留: {geek}")

    print(f"\nASSET ROTATION {'OK' if not fails else 'FAIL'}（抽样 {args.runs} 次）")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
