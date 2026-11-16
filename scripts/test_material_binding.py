#!/usr/bin/env python3
"""物料硬约束回归（A 硬绑定 / B 自证 / C 审计）—— 全离线，不打 LLM。

覆盖：
1. 真机事故复现：物料 tags 被整组顶替 → 绑定后物料 5 个全在，生成只补 1 个到上限 6；
2. title 硬绑定：物料 title 非空时覆盖 LLM 重写；
3. 超限 fail-closed：物料 title >20 → `TITLE_TOO_LONG`；物料 tags >6 → `TAGS_OVER_LIMIT`；
   两者都**不许**静默截断；
4. dedupe / 生成 tags 与物料重复时不重复补位；
5. 审计记录：material / generated / bound / final / checks / conclusion 全在；
6. 集成：`exec_expand_script`（monkeypatch LLM）→ draft_script.json 的 title/tags 来自物料，
   且 material_binding.json 落盘；物料超限时抛 ExecutorError 且不写 draft_script.json。

用法：python3 scripts/test_material_binding.py [-v]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

VERBOSE = "-v" in sys.argv
RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    if VERBOSE or not cond:
        print(f"{'PASS' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


MAT = {"title": "Vibe知识大赏：AI学习越用越笨？",
       "tags": ["vibe知识大赏", "ai学习工具", "大学生", "AI教育", "学习方法"]}
GEN = {"title": "AI学习越用越笨？98%大学生中招",
       "tags": ["AI教育", "大学生", "AI依赖", "学习效率", "科技调查"]}


def test_binding() -> None:
    from src.pipeline.material_binding import MAX_TAGS, apply_binding, bind_tags, dedupe

    bound, audit = apply_binding(MAT, GEN)
    check("title 用物料值（覆盖 LLM 重写）", bound["title"] == MAT["title"], bound["title"])
    check(f"tags 截到上限 {MAX_TAGS}", len(bound["tags"]) == MAX_TAGS, str(bound["tags"]))
    check("物料 5 个 tags 全部保留在前",
          bound["tags"][:5] == MAT["tags"], str(bound["tags"][:5]))
    check("生成 tags 只补 1 个空位", bound["tags"][5] in GEN["tags"], bound["tags"][5])
    check("审计记了 title 被改写", audit["changed"]["title"] is True)
    check("审计记了物料/生成/绑定三段",
          audit["material"]["tags"] == MAT["tags"] and audit["generated"]["tags"] == GEN["tags"]
          and audit["bound"]["tags"] == bound["tags"])
    check("审计 title_source=material", audit["title_source"] == "material")

    # 生成 tags 与物料完全重复 → 不重复补位
    dup = bind_tags(["a", "b"], ["b", "a", "c"])
    check("dedupe 不重复补位", dup == ["a", "b", "c"], str(dup))
    check("空物料 tags 时纯用生成", bind_tags([], ["x", "y"]) == ["x", "y"])
    check("去空白/去空串", dedupe([" a ", "", "a", None, "b"]) == ["a", "b"],
          str(dedupe([" a ", "", "a", None, "b"])))


def test_fail_closed() -> None:
    from src.pipeline.material_binding import (MAX_TAGS, MaterialBindingError, TITLE_MAX,
                                               apply_binding, validate_bound)

    long_title = "Vibe知识大赏：AI学习越用越笨？大学生集体中招现场实录"
    try:
        apply_binding({"title": long_title, "tags": ["x"]}, GEN)
        check(f"物料标题 >{TITLE_MAX} → 报错", False, "没有抛错（静默截断？）")
    except MaterialBindingError as exc:
        check(f"物料标题 >{TITLE_MAX} → TITLE_TOO_LONG",
              any(c.startswith("TITLE_TOO_LONG") for c in exc.codes), str(exc.codes))

    try:
        apply_binding({"title": "短标题", "tags": [f"t{i}" for i in range(MAX_TAGS + 1)]}, GEN)
        check(f"物料 tags >{MAX_TAGS} → 报错", False, "没有抛错（静默丢弃？）")
    except MaterialBindingError as exc:
        check(f"物料 tags >{MAX_TAGS} → TAGS_OVER_LIMIT",
              any(c.startswith("TAGS_OVER_LIMIT") for c in exc.codes), str(exc.codes))

    # 自证：产物里少了物料 tags / title 被换 → 必须报出来
    codes = validate_bound(MAT, {"title": GEN["title"], "tags": GEN["tags"]})
    check("产物 title 被换 → TITLE_REWRITTEN", "TITLE_REWRITTEN" in codes, str(codes))
    check("产物丢了物料 tag → TAGS_DROPPED", any(c.startswith("TAGS_DROPPED") for c in codes),
          str(codes))
    ok = validate_bound(MAT, {"title": MAT["title"], "tags": MAT["tags"]})
    check("完全一致 → 无错", ok == [], str(ok))

    # 物料没写 title 时不强行判 TITLE_REWRITTEN（生成值就是唯一来源）
    check("物料无 title → 不判 TITLE_REWRITTEN",
          validate_bound({"tags": ["a"]}, {"title": "模型标题", "tags": ["a"]}) == [])


def test_executor_integration(tmp: Path) -> None:
    """集成：跑 exec_expand_script（LLM 换成假实现），看落盘与报错行为。"""
    from src.pipeline import executors as ex

    fake = {"theme": "tech-github", "entity_key": "ai-study", "title": GEN["title"],
            "cover_main_text": "AI学习", "cover_sub_text": "98%大学生中招",
            "hook_narration": "你可能正在越学越笨", "fact_summary_150": "事实摘要" * 10,
            "video_desc": "简介正文", "resource_keyword": "AI学习",
            "scenes": [{"scene_index": i, "segment_narration": f"第{i}段",
                        "visual_type": "ui_card", "highlight_text": "重点",
                        "sfx_cue": "none"} for i in range(3)],
            "tags": GEN["tags"], "target_account_type": "tech"}

    import src.hotspot_extractor as hx
    orig = hx.pick_and_convert_hotspot
    hx.pick_and_convert_hotspot = lambda *a, **k: dict(fake)   # type: ignore

    run_root = tmp / "run1"
    run_root.mkdir(parents=True)
    ctx = ex.StepContext(step={"id": "expand_script"}, run_root=run_root, run_id="bind-1",
                         task_id="t", material=MAT, db_path=str(tmp / "s.db"))
    ex.exec_expand_script(ctx)

    draft = json.loads((run_root / "draft_script.json").read_text(encoding="utf-8"))
    audit = json.loads((run_root / "material_binding.json").read_text(encoding="utf-8"))
    check("集成：draft 的 title 来自物料", draft["title"] == MAT["title"], draft["title"])
    check("集成：draft 的 tags 含全部物料 tags",
          all(t in draft["tags"] for t in MAT["tags"]), str(draft["tags"]))
    check("集成：tags 数量 ≤6", len(draft["tags"]) <= 6, str(len(draft["tags"])))
    check("集成：material_binding.json 落盘且结论 PASS",
          audit.get("conclusion") == "PASS", str(audit.get("conclusion")))

    # 负向：物料标题超限 → ExecutorError，且不写 draft_script.json
    run2 = tmp / "run2"
    run2.mkdir()
    ctx2 = ex.StepContext(step={"id": "expand_script"}, run_root=run2, run_id="bind-2",
                          task_id="t",
                          material={"title": "超长" * 15, "tags": ["a"]},
                          db_path=str(tmp / "s.db"))
    raised = ""
    try:
        ex.exec_expand_script(ctx2)
    except ex.ExecutorError as exc:
        raised = str(exc)
    check("集成：物料超限 → ExecutorError（提示改物料）",
          "物料硬约束不通过" in raised and "TITLE_TOO_LONG" in raised, raised[:80])
    check("集成：物料超限时不产出 draft_script.json",
          not (run2 / "draft_script.json").exists())

    hx.pick_and_convert_hotspot = orig


def test_real_material_fixture() -> None:
    """真机事故对照：ai-study-survey-1 的物料与当时产物（产物丢了「学习方法」）。

    夹具可能只存在于主 checkout（worktree 里没有 output/），找不到就 SKIP。
    """
    cands = [PROJ, Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")]
    for base in cands:
        mat_p = base / "materials" / "20261115_ai_study_survey.json"
        draft_p = base / "output" / "20261115" / "ai-study-survey-1" / "draft_script.json"
        if mat_p.exists() and draft_p.exists():
            break
    else:
        print("SKIP 真机物料对照（夹具不在本 worktree）")
        return

    from src.pipeline.material_binding import apply_binding
    material = json.loads(mat_p.read_text(encoding="utf-8"))
    generated = json.loads(draft_p.read_text(encoding="utf-8"))
    generated = generated.get("draft") or generated
    bound, audit = apply_binding(material, generated)
    lost = [x for x in material.get("tags") or [] if x not in (generated.get("tags") or [])]
    check(f"真机：当时产物确实丢了物料 tag（{lost}）", bool(lost), str(lost))
    check("真机：绑定后物料 tags 一个不少",
          all(x in bound["tags"] for x in material.get("tags") or []), str(bound["tags"]))
    check("真机：绑定后 title 回到物料值",
          bound["title"] == material["title"], bound["title"])


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        test_binding()
        test_fail_closed()
        test_executor_integration(Path(d))
        test_real_material_fixture()
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, det) for l, c, det in RESULTS if not c]
    print(f"\nMATERIAL BINDING {ok} PASS / {len(bad)} FAIL")
    for l, det in bad:
        print(f"  ✗ {l} {det}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
