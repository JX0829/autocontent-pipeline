#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课文故事 M1 契约回归（F-2026-11-12-TEXTBOOK-STORY §8.2）

固化 M1 四条改动的判据，防止「科技链路被改动波及」和「课文物料放行劣质片」回归：

  T1  分镜上限：scenes ≤25（课文）／仍 ≤8（科技，回归不变）
  T2  物料校验：textbook_story 的 textbook/facts 必填、facts≥3、每条 source 非空
  T3  prompt：target_duration_sec 与「镜数 = 时长 / 8~10 秒」真的写进了提示词
  T4  时长断言：出片时长窗口（90~150s ∩ 目标 ±15%）
  T5  账号类型：edu 可用，非法值仍被拒

特点：不联网、不渲染、不调模型 —— 与 verify_all.sh --fast 同档。
用法：python3 scripts/test_textbook_story.py [-v]
退出码：0 全过（含 SKIP）；1 有 FAIL
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ / "scripts" / "hooks"))

VERBOSE = "-v" in sys.argv
PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  ✅ {name}" + (f"  {detail}" if VERBOSE and detail else ""))


def bad(name: str, detail: str) -> None:
    FAIL.append(name)
    print(f"  ❌ {name}  {detail}")


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print(f"  ⏭️  {name}  SKIP（{why}）")


def expect_raises(name: str, fn, exc_types, hint: str = "") -> None:
    try:
        fn()
    except exc_types as exc:  # noqa: BLE001 — 断言型测试，就是要捕获
        ok(name, f"{type(exc).__name__}: {str(exc)[:70]}")
    except Exception as exc:  # noqa: BLE001
        bad(name, f"错误类型不对: {type(exc).__name__}: {exc}")
    else:
        bad(name, f"未拦截{hint}")


# ------------------------------------------------------------------ 样例构造

INK_TYPES = ("ink_title", "vertical_poem", "ancient_map", "archive_card", "scroll_transition")


def _scene(i: int, narration: str = "他六十八岁那年写下僵卧孤村不自哀，夜里做梦还在边关。",
           visual: str = "") -> dict:
    return {
        "scene_index": i,
        "segment_narration": narration[:100],
        # 课文片只允许古风五类；科技片沿用 ui_card
        "visual_type": visual or "ui_card",
        "code_snippet": None,
        "media_asset_url": None,
        "stat_items": None,
        "highlight_text": f"第{i + 1}镜",
        "sfx_cue": "none",
    }


def _draft(n: int, content_type: str = "tech_news", acct: str = "tech",
           theme: str = "tech-github") -> dict:
    return {
        "theme": theme,
        "entity_key": "textbook:shier" if content_type == "textbook_story" else "news:demo",
        "content_type": content_type,
        "title": "陆游临终前写的28个字",
        "cover_main_text": "临终28字",
        "cover_sub_text": "课文背后",
        "hook_narration": "八十五岁，他写下最后二十八个字。",
        "fact_summary_150": "陆游临终前作《示儿》，叮嘱儿子收复中原后祭告。",
        "video_desc": "讲一首课文背后的故事。" * 8,
        "resource_keyword": "shier",
        "template_id": "auto",
        "scenes": [
            _scene(i, visual=INK_TYPES[i % len(INK_TYPES)] if content_type == "textbook_story" else "ui_card")
            for i in range(n)
        ],
        "tags": ["示儿", "陆游"],
        "target_account_type": acct,
    }


def _manifest() -> dict:
    return {
        "content_type": "textbook_story",
        "title": "陆游临终前写的28个字",
        "body": "《示儿》是陆游临终前写给儿子的最后几句话，他一生主张北伐收复中原。" * 3,
        "source_url": "https://zh.wikisource.org/wiki/demo",
        "textbook": {"poem_title": "示儿", "dynasty": "宋", "author": "陆游",
                     "grade": "五年级", "volume": "上册", "edition": "部编版",
                     "full_text": "死去元知万事空，但悲不见九州同。"},
        "facts": [
            {"claim": "陆游生于1125年，卒于1210年", "source": "《宋史·陆游传》"},
            {"claim": "《示儿》作于嘉定二年冬，临终前", "source": "钱仲联《剑南诗稿校注》"},
            {"claim": "靖康之变发生于1127年", "source": "《宋史·钦宗本纪》"},
        ],
        "storyboard_hint": {"target_duration_sec": 120, "scene_count": 14},
        "compliance": {"ai_generated_label": True, "textbook_attribution": "部编版五年级上册"},
    }


# ═══════════════════════════════ T1 分镜上限
def test_scene_budget() -> None:
    print("\n[T1] 分镜上限：课文 ≤25 / 科技仍 ≤8")
    from pydantic import ValidationError

    from src.schemas import DraftScriptPayload

    try:
        p = DraftScriptPayload(**_draft(16, "textbook_story", "edu", "textbook-story"))
        ok("课文故事 16 镜通过", f"scenes={len(p.scenes)}")
    except Exception as exc:  # noqa: BLE001
        bad("课文故事 16 镜通过", f"误杀: {exc}")

    for n in (9, 12, 25):
        try:
            DraftScriptPayload(**_draft(n, "textbook_story", "edu", "textbook-story"))
            ok(f"课文故事 {n} 镜通过")
        except Exception as exc:  # noqa: BLE001
            bad(f"课文故事 {n} 镜通过", f"误杀: {exc}")

    # 镜数下限不在 schema 卡（30s 样片 3 镜也合法），由生成期的「时长÷8~10s」预算断言负责：
    try:
        DraftScriptPayload(**_draft(3, "textbook_story", "edu", "textbook-story"))
        ok("课文故事 3 镜通过（30s 样片场景，下限交给生成期预算）")
    except Exception as exc:  # noqa: BLE001
        bad("课文故事 3 镜通过（30s 样片场景，下限交给生成期预算）", f"误杀: {exc}")

    expect_raises("课文故事 26 镜被拒（超 25 上限）",
                  lambda: DraftScriptPayload(**_draft(26, "textbook_story", "edu", "textbook-story")),
                  ValidationError, "超上限")

    # —— 科技链路回归：8 镜仍合法，9 镜仍被拒
    for n in (3, 4, 8):
        try:
            DraftScriptPayload(**_draft(n))
            ok(f"科技速报 {n} 镜通过（回归）")
        except Exception as exc:  # noqa: BLE001
            bad(f"科技速报 {n} 镜通过（回归）", f"误杀: {exc}")
    expect_raises("科技速报 9 镜仍被拒（回归）",
                  lambda: DraftScriptPayload(**_draft(9)), ValidationError, "科技速报分镜")

    # 缺省 content_type 的旧 payload（无该字段）必须仍按科技处理
    legacy = _draft(8)
    legacy.pop("content_type")
    try:
        DraftScriptPayload(**legacy)
        ok("缺省 content_type 视为 tech_news（回归）")
    except Exception as exc:  # noqa: BLE001
        bad("缺省 content_type 视为 tech_news（回归）", f"误杀: {exc}")


# ═══════════════════════════════ T2 物料校验
def test_manifest_rules() -> None:
    print("\n[T2] 物料校验：textbook/facts 必填、facts≥3、source 非空")
    from src.pipeline.manifest import content_type_of, validate, validate_textbook_story

    if not validate(_manifest()):
        ok("合规课文物料通过")
    else:
        bad("合规课文物料通过", f"误杀: {validate(_manifest())}")

    # 缺 source 必须报错（spec §8.2 硬判据）
    m = _manifest()
    m["facts"][1]["source"] = ""
    errs = validate(m)
    if any("source" in e and "facts[1]" in e for e in errs):
        ok("facts 缺 source 被拒", errs[0])
    else:
        bad("facts 缺 source 被拒", f"未命中: {errs}")

    m = _manifest()
    m["facts"] = m["facts"][:2]
    errs = validate(m)
    if any("至少需要 3 条" in e for e in errs):
        ok("facts 少于 3 条被拒", errs[0])
    else:
        bad("facts 少于 3 条被拒", f"未命中: {errs}")

    m = _manifest()
    m.pop("facts")
    if any("facts" in e for e in validate(m)):
        ok("facts 缺失被拒")
    else:
        bad("facts 缺失被拒", f"{validate(m)}")

    m = _manifest()
    m.pop("textbook")
    if any("textbook" in e for e in validate(m)):
        ok("textbook 缺失被拒")
    else:
        bad("textbook 缺失被拒", f"{validate(m)}")

    m = _manifest()
    m["textbook"]["poem_title"] = "  "
    if any("poem_title" in e for e in validate(m)):
        ok("poem_title 为空被拒")
    else:
        bad("poem_title 为空被拒", f"{validate(m)}")

    m = _manifest()
    m["storyboard_hint"] = {"target_duration_sec": "两分钟"}
    if any("target_duration_sec" in e for e in validate(m)):
        ok("target_duration_sec 非整数被拒")
    else:
        bad("target_duration_sec 非整数被拒", f"{validate(m)}")

    # 科技物料（无 content_type）回归：结果必须仍为空
    tech = {"title": "开源项目 5B 参数跑通多模态", "source_url": "https://example.com/x"}
    if validate(tech) == []:
        ok("科技物料校验不变（回归）")
    else:
        bad("科技物料校验不变（回归）", f"{validate(tech)}")

    if content_type_of({"title": "x"}) == "tech_news" and content_type_of(_manifest()) == "textbook_story":
        ok("content_type_of 缺省口径正确")
    else:
        bad("content_type_of 缺省口径正确", "口径不一致")

    if validate_textbook_story(tech) == []:
        ok("非课文物料跳过课文规则（回归）")
    else:
        bad("非课文物料跳过课文规则（回归）", "科技物料被课文规则干扰")

    # 真实 PM 物料（存在则校验，不存在 SKIP）：按 glob 覆盖全部课文物料，
    # 保证 PM 新写进 materials/ 的篇目立刻被契约扫到，而不是靠人记得加用例。
    textbook_materials = sorted((PROJ / "materials").glob("*textbook*.json"))
    if not textbook_materials:
        skip("PM 课文物料", "materials/*textbook*.json 为空")
    for p in textbook_materials:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bad(f"PM 物料 {p.name} 通过", f"JSON 非法: {exc}")
            continue
        errs = validate(data)
        if errs:
            bad(f"PM 物料 {p.name} 通过", f"{errs}")
        else:
            ok(f"PM 物料 {p.name} 通过", f"facts={len(data.get('facts') or [])}")


# ═══════════════════════════════ T3 prompt 注入
def test_prompt_injection() -> None:
    print("\n[T3] prompt 注入：target_duration_sec + 镜数 = 时长 / 8~10 秒")
    from src.hotspot_extractor import (TEXTBOOK_SYSTEM_PROMPT, build_textbook_prompt,
                                       is_textbook_story, textbook_scene_budget)

    if textbook_scene_budget(90) == (9, 10, 11) and textbook_scene_budget(120) == (12, 13, 15) \
            and textbook_scene_budget(150) == (15, 17, 18):
        ok("镜数预算公式正确 90/120/150", f"{textbook_scene_budget(90)} {textbook_scene_budget(120)} {textbook_scene_budget(150)}")
    else:
        bad("镜数预算公式正确 90/120/150",
            f"{textbook_scene_budget(90)} {textbook_scene_budget(120)} {textbook_scene_budget(150)}")

    system, user = build_textbook_prompt(_manifest())
    checks = {
        "target_duration_sec 注入": "target_duration_sec=120" in user,
        "镜数区间注入": "12~15 镜" in system and "建议 13 镜" in system,
        "字数区间注入": "432~675 字" in system,
        "facts 出处注入": "《宋史·陆游传》" in user,
        "六段脚本注入": "六段" in system,
        "禁止 terminal_code": "terminal_code" in system,
        "禁止科技号套话结尾": "欢迎在评论区交流讨论" in system,
        # M2：五类古风视觉必须出现在提示词里，模型才知道能用什么
        **{f"注入古风视觉 {t}": t in system for t in INK_TYPES},
    }
    for name, okk in checks.items():
        (ok if okk else bad)(name, "" if okk else "提示词里没找到对应约束")

    for fmt in ("{target}", "{lo}", "{total_lo}", "{mid}", "{chars_lo}"):
        if fmt in system:
            bad("system prompt 占位符已全部渲染", f"残留 {fmt}")

    if is_textbook_story(_manifest()) and not is_textbook_story({"title": "x"}):
        ok("is_textbook_story 分流正确（科技物料不误入）")
    else:
        bad("is_textbook_story 分流正确（科技物料不误入）", "分流错误")

    # 缺 storyboard_hint → 用缺省 120 秒，prompt 仍可用
    m = _manifest()
    m.pop("storyboard_hint")
    _, user2 = build_textbook_prompt(m)
    if "target_duration_sec=120" in user2:
        ok("缺 storyboard_hint 时按缺省 120 秒")
    else:
        bad("缺 storyboard_hint 时按缺省 120 秒", user2[-160:])


# ═══════════════════════════════ T4 时长断言
def test_duration_guard() -> None:
    print("\n[T4] 出片时长断言窗口（90~150s ∩ 目标 -30%~+30%）")
    from textbook_duration_guard import duration_window

    import subprocess
    import tempfile

    # 用户口径：低于目标 70% 直接判失败；上限仍封顶 150s
    cases = {90: (90.0, 117.0), 120: (90.0, 150.0), 150: (105.0, 150.0)}
    for target, want in cases.items():
        got = duration_window(target)
        if got == want:
            ok(f"{target}s 目标窗口 {got}")
        else:
            bad(f"{target}s 目标窗口 {got}", f"期望 {want}")

    # 真实 ffprobe 路径：合成 2 秒片 → 课文 120s 目标必须被拦
    try:
        from textbook_duration_guard import main as guard_main  # noqa: F401
        tmp = Path(tempfile.mkdtemp())
        short = tmp / "short.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=160x120:rate=8:duration=2",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(short),
                        "-loglevel", "error"], check=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        skip("2 秒片被时长断言拦截", f"无法合成测试片: {exc}")
        return

    payload = {
        "task_id": "t", "run_id": "t", "step_id": "render_vertical", "run_root": str(tmp),
        "artifacts": {"video": str(short)},
        "context": {"manifest": _manifest(), "video_key": "video"},
    }
    script = PROJ / "scripts" / "hooks" / "textbook_duration_guard.py"
    proc = subprocess.run([sys.executable, str(script)], input=json.dumps(payload, ensure_ascii=False),
                          capture_output=True, text=True, timeout=90)
    try:
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        bad("2 秒片被时长断言拦截", f"hook 输出非 JSON: {proc.stdout[:120]} {proc.stderr[:120]}")
        return
    if out.get("ok") is False and "过短" in str(out.get("reason")):
        ok("2 秒片被时长断言拦截", str(out.get("reason"))[:60])
    else:
        bad("2 秒片被时长断言拦截", f"{out}")

    # 科技物料的同一支 2 秒片必须放行（回归：不做时长断言）
    payload_tech = {
        "task_id": "t", "run_id": "t", "step_id": "render_vertical", "run_root": str(tmp),
        "artifacts": {"video": str(short)},
        "context": {"manifest": {"title": "科技速报样例", "body": "测试正文" * 10}, "video_key": "video"},
    }
    proc = subprocess.run([sys.executable, str(script)], input=json.dumps(payload_tech, ensure_ascii=False),
                          capture_output=True, text=True, timeout=90)
    try:
        out = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        bad("科技物料放行（回归）", f"hook 输出非 JSON: {proc.stdout[:120]}")
        return
    if out.get("ok") is True:
        ok("科技物料放行（回归）", str(out.get("reason"))[:50])
    else:
        bad("科技物料放行（回归）", f"{out}")


# ═══════════════════════════════ T5 账号类型 + 声明层接线
def test_edu_and_wiring() -> None:
    print("\n[T5] edu 账号类型 + 声明层接线")
    from pydantic import ValidationError

    from src.pipeline.contracts import load_pipeline, validate_structure
    from src.schemas import DraftScriptPayload

    for acct in ("tech", "news", "edu"):
        try:
            DraftScriptPayload(**_draft(4, acct=acct))
            ok(f"target_account_type={acct} 通过")
        except Exception as exc:  # noqa: BLE001
            bad(f"target_account_type={acct} 通过", f"误杀: {exc}")

    expect_raises("target_account_type=xhs 被拒",
                  lambda: DraftScriptPayload(**_draft(4, acct="xhs")), ValidationError, "非法账号类型")

    errors = validate_structure(load_pipeline(), check_impls=True)
    if errors:
        bad("pipeline.yaml 结构合法（含新 hook 的 impl 存在）", "; ".join(errors[:3]))
    else:
        ok("pipeline.yaml 结构合法（含新 hook 的 impl 存在）")

    import yaml
    accounts = yaml.safe_load((PROJ / "config" / "accounts.yaml").read_text(encoding="utf-8"))
    edu = [a for a in accounts.get("accounts") or [] if a.get("account_type") == "edu"]
    if edu:
        ok("accounts.yaml 已配 edu 账号", ", ".join(a["id"] for a in edu))
    else:
        bad("accounts.yaml 已配 edu 账号", "未找到 account_type=edu")


# ═══════════════════════════════ T6 古风视觉隔离（M2）
def test_ink_visual_isolation() -> None:
    print("\n[T6] 古风视觉与科技链路隔离（M2）")
    from pydantic import ValidationError

    from src.schemas import DraftScriptPayload

    # 课文片禁止科技风视觉（判据：画面里不得出现 terminal_code/stat_chart/ui_card）
    for tech_visual in ("terminal_code", "ui_card", "stat_chart", "media_showcase"):
        expect_raises(
            f"课文片含 {tech_visual} 被拒",
            lambda v=tech_visual: DraftScriptPayload(**_draft_with_visual(
                12, "textbook_story", "edu", "textbook-story", v)),
            ValidationError, "科技风视觉")

    # 科技片禁止古风视觉（不许悄悄把科技片的画面换成国风）
    for ink_visual in INK_TYPES:
        expect_raises(
            f"科技片含 {ink_visual} 被拒（回归）",
            lambda v=ink_visual: DraftScriptPayload(**_draft_with_visual(4, "tech_news", "tech", "tech-github", v)),
            ValidationError, "古风视觉")

    # 五类古风视觉各自单独建片都必须通过
    for ink_visual in INK_TYPES:
        try:
            DraftScriptPayload(**_draft_with_visual(12, "textbook_story", "edu", "textbook-story", ink_visual))
            ok(f"课文片使用 {ink_visual} 通过")
        except Exception as exc:  # noqa: BLE001
            bad(f"课文片使用 {ink_visual} 通过", f"误杀: {exc}")

    # 渲染层真的注册了古风合成（不是只改了 schema 却忘记接线）
    root = (PROJ / "renderer" / "src" / "Root.tsx").read_text(encoding="utf-8")
    for comp in ("TextbookStoryVideo", "TextbookLandscapeVideo",
                 "TextbookCoverStill", "TextbookLandscapeCoverStill"):
        if f'id="{comp}"' in root:
            ok(f"Remotion 已注册 {comp}")
        else:
            bad(f"Remotion 已注册 {comp}", "Root.tsx 里找不到该 composition")

    # 科技合成仍在（不许替换）
    for comp in ("GeekShortVideo", "BilibiliLandscapeVideo"):
        if f'id="{comp}"' in root:
            ok(f"科技合成保留 {comp}（回归）")
        else:
            bad(f"科技合成保留 {comp}（回归）", "科技合成被删/改名了")

    ex = (PROJ / "src" / "pipeline" / "executors.py").read_text(encoding="utf-8")
    if "TextbookStoryVideo" in ex and "GeekShortVideo" in ex:
        ok("executors 按 content_type 分流合成")
    else:
        bad("executors 按 content_type 分流合成", "executors 里没看到两套合成名")

    # 30s 样片预算：3 镜整
    from src.hotspot_extractor import textbook_scene_budget
    if textbook_scene_budget(30) == (3, 3, 3):
        ok("30s 样片镜数预算 (3,3,3)")
    else:
        bad("30s 样片镜数预算 (3,3,3)", str(textbook_scene_budget(30)))


def _draft_with_visual(n: int, content_type: str, acct: str, theme: str, visual: str) -> dict:
    d = _draft(n, content_type, acct, theme)
    d["scenes"] = [{**_scene(i, visual=visual)} for i in range(n)]
    return d


def main() -> int:
    print("=== 课文故事 M1+M2 契约回归（F-2026-11-12）===")
    test_scene_budget()
    test_manifest_rules()
    test_prompt_injection()
    test_duration_guard()
    test_edu_and_wiring()
    test_ink_visual_isolation()

    print("\n──────── 汇总 ────────")
    print(f"  ✅ {len(PASS)}  ❌ {len(FAIL)}  ⏭️  {len(SKIP)}")
    for f in FAIL:
        print(f"  ❌ {f}")
    if FAIL:
        print("\nTEXTBOOK M1 FAIL")
        return 1
    print("\nTEXTBOOK M1 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
