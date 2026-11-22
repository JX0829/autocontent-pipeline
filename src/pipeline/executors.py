#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P5 前置：runner 的 step 执行体接线

把 `pipeline.yaml` 里声明的 7 个 step 真正接到已有组件上，让
`python3 -m src.pipeline.runner --material <json>` 能跑通并解锁 GATE-3。

设计约束：
  · 执行体只做「编排」，业务逻辑仍在原组件里（intake / manifest / hotspot_extractor /
    TTSAligner / remotion / approval_service），不复制实现
  · 所有产物写进 `run_root`，这样 `provenance_check`（产物必须来自本次 run）才有意义
  · 每个执行体返回 `{artifact_key: path}`，由 runner 汇总后喂给 hook
  · 失败一律抛 `ExecutorError`，由 runner 记 BLOCKED + 退出码 64，不静默
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROJ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJ))

RENDERER = PROJ / "renderer"
REMOTION = RENDERER / "node_modules" / ".bin" / "remotion"


class ExecutorError(RuntimeError):
    """执行体失败（runner 记 BLOCKED，退出码 64）。"""


@dataclass
class StepContext:
    step: dict[str, Any]
    run_root: Path
    run_id: str
    task_id: str
    material: dict[str, Any] | None
    artifacts: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    db_path: str = ""      # 本次 run 的状态库；空则回退 SQLITE_DB_PATH / 默认库

    def out(self, name: str) -> Path:
        return self.run_root / name


# ---------------------------------------------------------------- 工具

def _write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _assert_has_audio(path: Path) -> None:
    """产物必须含音频流，否则是无声片 —— 这类缺陷肉眼在缩略图上完全看不出来。

    实测背景：T4.1 把 public 根换到 run 目录后，只要软链缺失，Remotion 会
    渲染出**没有音轨**的视频而不报错（ffprobe 只看到 h264）。所以这里硬断言。
    """
    path = Path(path)   # 容忍 str 入参：否则 path.name 会抛 AttributeError 掩盖真实错误
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            text=True, timeout=60,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        raise ExecutorError(f"音频流探测失败（{path.name}）: {exc}") from exc
    if not out:
        raise ExecutorError(
            f"产物无音频流（无声片）: {path.name} —— 检查 run 目录软链 "
            f"renderer/public/runs/<run_id> 是否指向 props，以及 sfx 是否可达")


def _remotion(*args: str, timeout: int = 900) -> None:
    """调用本地 remotion 二进制（`npx remotion` 在本机解析失败，必须走 node_modules/.bin）。"""
    cmd = [str(REMOTION), *args]
    if not REMOTION.exists():
        raise ExecutorError(f"remotion 二进制缺失: {REMOTION}（先 npm install）")
    proc = subprocess.run(cmd, cwd=str(RENDERER), capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise ExecutorError(f"remotion 渲染失败（exit={proc.returncode}）: {tail}")



# ---------------------------------------------------------------- 模板/素材
# template_id -> Remotion composition。老模板全部继续走 GeekShortVideo（归档不删），
# 新增模板只需在这里加一行；回滚只需把 config/templates.yaml 的 theme_mapping 改回去。
COMPOSITION_BY_TEMPLATE = {
    "tech_story": "TechStoryVideo",
    "textbook_story": "TextbookStoryVideo",
    # 2026-11-19（SPEC 方案 C）：水墨本模板
    "ink_story": "InkStoryVideo",
    # 2026-11-18 用户要求下线旧 GeekShortVideo 排版模板：T1~T5 一律落到 tech_story，
    # 不再有任何路径渲染 GeekShortVideo（文件保留，回滚=删这几行）。
    "editorial_news": "TechStoryVideo",
    "cupertino_glass": "TechStoryVideo",
    "neo_brutalism": "TechStoryVideo",
    "notion_craft": "TechStoryVideo",
    "linear_obsidian": "TechStoryVideo",
}
DEFAULT_VERTICAL_COMPOSITION = "TechStoryVideo"


def _composition_for(props_data: dict[str, Any]) -> str:
    # 课文物料以 content_type 为准（template_id 可能仍是 tech 默认值）
    if str(props_data.get("content_type") or "") == "textbook_story":
        return "TextbookStoryVideo"
    return COMPOSITION_BY_TEMPLATE.get(str(props_data.get("template_id")), DEFAULT_VERTICAL_COMPOSITION)


def _attach_assets(ctx: StepContext, props_data: dict[str, Any],
                   library: str | None = None) -> None:
    """按素材库配置选素材，注入 props 并落 asset_binding.json 审计。

    2026-11-17（SPEC-20261117 R2/R3/R5）：
      · 传 draft 语义给选材（title / tags / 首场景旁白）→ 判档位，不再「池里唯一那个」；
      · 物料里写 `open_asset` / `bg_asset` / `cover_asset` 就直接锁死（R3）；
      · 无匹配档位 → 不借用别的档位，回退纯排版封面 + NO_MATCHING_ASSET_FALLBACK（R5）。
    """
    from src.assets_manager import AssetManager

    draft: dict[str, Any] = {}
    draft_path = ctx.artifacts.get("draft_script") or str(ctx.run_root / "draft_script.json")
    try:
        raw = json.loads(Path(draft_path).read_text(encoding="utf-8"))
        draft = raw.get("draft") or raw
    except Exception as exc:  # noqa: BLE001 — 读不到就当无语义信号，走 R5 回退
        print(f"[assets] WARN 读不到 draft_script（{type(exc).__name__}），语义选材跳过")

    man = ctx.material or {}
    locked = {slot: str(man[key]) for slot, key in
              (("open", "open_asset"), ("bg", "bg_asset"), ("cover", "cover_asset"))
              if man.get(key)}

    sel = AssetManager().select(run_id=ctx.run_id, library=library,
                                draft=draft, locked=locked)
    # 水墨库要把 mid/end 真正交给渲染层（课文本靠它们播飞鸟/泼墨/残墨）
    props_data.update(sel.to_render_props(inject_mid_end=(library == "ink_story")))
    audit = sel.to_audit()
    props_data["asset_binding"] = audit
    _write_json(ctx.run_root / "asset_binding.json", audit)
    for w in sel.warnings:
        print(f"[assets] WARN {w}")
    print(f"[assets] 档位={sel.domain or '未判定'} 封面形态={sel.cover_mode} "
          f"open={sel.open.id if sel.open else '（无）'} bg={sel.bg.id} "
          f"mid={sel.mid.id if sel.mid else '（无）'} end={sel.end.id if sel.end else '（无）'} "
          f"bgm={sel.bgm.id if sel.bgm else '（无）'}")


# ---------------------------------------------------------------- 执行体


def assert_textbook_authorized(is_literary: bool, material: dict | None, template_id: str = "") -> None:
    """课文/水墨模板默认禁用（用户口径 2026-11-22）。

    「除非强制要求使用课文背后故事，否则不能使用对应的模板和内容」——
    判定为文学类后，必须物料显式 `force_textbook: true`（或 ALLOW_TEXTBOOK=1）才放行。
    """
    if not is_literary:
        return
    man = material or {}
    if man.get("force_textbook") is True or os.environ.get("ALLOW_TEXTBOOK") == "1":
        return
    raise ExecutorError(
        "TEXTBOOK_REQUIRES_EXPLICIT：课文/水墨模板默认禁用。"
        "确实要做课文背后故事，请在物料里加 \"force_textbook\": true 后重跑"
        f"（当前 template_id={template_id or 'auto'}）")


def exec_intake(ctx: StepContext) -> dict[str, str]:
    """builtin.intake：物料已在 runner 入口校验过，这里落库到 run_root。"""
    if not ctx.material:
        raise ExecutorError("没有可用物料（--material /dev/null 不能进入真实执行）")
    man = _write_json(ctx.out("manifest.json"), ctx.material)
    return {"manifest": str(man)}


def exec_check_material(ctx: StepContext) -> dict[str, str]:
    """builtin.check_material：按 manifest.schema.json 二次校验 + 产出检查摘要。"""
    from src.pipeline.manifest import load_manifest, validate

    man_path = ctx.artifacts.get("manifest") or str(ctx.out("manifest.json"))
    data = json.loads(Path(man_path).read_text(encoding="utf-8"))
    errors = validate(data)          # 返回错误列表（空 = 通过）
    if errors:
        raise ExecutorError("物料校验失败: " + "; ".join(errors))

    body = str(data.get("body") or "")
    src = str(data.get("source_url") or "")
    report = {
        "ok": True,
        "title": data.get("title"),
        "body_chars": len(body),
        "has_source_url": src.startswith(("http://", "https://")),
        "account": data.get("account"),
        "errors": [],
    }
    out = _write_json(ctx.out("material_check.json"), report)
    return {"material_check": str(out)}


def exec_expand_script(ctx: StepContext) -> dict[str, str]:
    """llm.main：把物料扩写成 DraftScriptPayload（走 LLMClient，槽位来自 pipeline.yaml）。

    重试不在这里做：`pipeline.yaml` 的 `expand_script.retry.max` 由 `runner._execute_step`
    统一读取（小品 PR #1，2026-11-10），`llm/client.py` 另有 `_is_transient()` 区分
    超时/连接/429/5xx。此处只负责「扩写 → schema 校验 → 落盘」，失败即抛。
    """
    from src.hotspot_extractor import pick_and_convert_hotspot
    from src.pipeline.material_binding import (MaterialBindingError, apply_binding,
                                               finish_report, validate_bound)
    from src.schemas import DraftScriptPayload

    man = ctx.material or {}
    news_text = "\n\n".join(x for x in [str(man.get("title") or ""), str(man.get("body") or "")] if x)
    if not news_text.strip():
        raise ExecutorError("物料没有可用于扩写的正文")

    # material 透传：content_type=textbook_story 时走课文分支（镜数预算 + target_duration 注入）
    data = pick_and_convert_hotspot(news_text, task_id=ctx.task_id, material=man)

    # ---- 物料硬约束（2026-11-15）：title 用物料的，tags 物料在前、生成只补位到 6 个 ----
    # 物料超限（title>20 / tags>6）→ 直接报错让物料侧改，**不静默截断**：
    # schemas 的 title validator 会无声截断，截断后就再也看不出「物料标题被改」了。
    try:
        bound, audit = apply_binding(man, data)
    except MaterialBindingError as exc:
        raise ExecutorError(f"物料硬约束不通过（需改物料，不是改代码）：{exc}") from exc

    payload = DraftScriptPayload(**bound)            # schema 校验（失败即抛，不兜底）
    final = payload.model_dump()

    # ---- 生成后自证：物料里的 title/tags 一项都不许少 ----
    codes = validate_bound(man, final)
    audit = finish_report(audit, final, codes)
    _write_json(ctx.out("material_binding.json"), audit)
    if audit["changed"]["title"] or audit["changed"]["tags"]:
        _added = len(audit["bound"]["tags"]) - len(audit["material"]["tags"])
        print(f"      · 物料绑定生效：title={audit['bound']['title']!r} "
              f"tags={audit['bound']['tags']}"
              f"（物料 {len(audit['material']['tags'])} 个全保留，生成补位 {max(_added, 0)} 个，"
              f"未用生成 tags：{audit['dropped_generated'] or '无'}）")
    if codes:
        raise ExecutorError(f"物料绑定自证失败：{'、'.join(codes)}（见 material_binding.json）")

    out = _write_json(ctx.out("draft_script.json"), final)
    return {"draft_script": str(out), "material_binding": str(ctx.out("material_binding.json"))}


def _load_draft(ctx: StepContext) -> dict[str, Any]:
    path = ctx.artifacts.get("draft_script") or str(ctx.out("draft_script.json"))
    if not Path(path).exists():
        raise ExecutorError(f"draft_script 不存在: {path}")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExecutorError(f"draft_script 不是合法 JSON: {exc}") from exc


def _prepare_props(ctx: StepContext, draft: dict[str, Any]) -> Path:
    """TTS 对齐 → {run_root}/props/（T4.1 隔离）+ public/runs/<run_id> 软链。

    软链必须在渲染前建好：Remotion 只经 public/ 供静态资源，
    缺链接会直接 readFile ENOENT（实测踩过）。
    """
    from src.schemas import DraftScriptPayload
    from src.tts.aligner import TTSAligner

    payload = DraftScriptPayload(**draft)

    # 分镜二次创作门禁：不达标立刻 BLOCKED，不浪费一次 TTS + 渲染
    # 2026-11-19（方案 C）：课文本 / 水墨本不走「新闻锐评」口径（诗词讲解没有"我们的看法"这种东西），
    # 门禁只对新闻/科技类生效；否则课文物料永远过不了门禁 → textbook 链路实际不可用。
    from src.utils.creator_take_guard import check_creator_takes, summarize
    _tpl = str((draft or {}).get("template_id") or "")
    _is_literary = (_tpl in ("textbook_story", "ink_story")
                    or str((ctx.material or {}).get("content_type") or "") in ("textbook_story", "ink_story")
                    or bool((ctx.material or {}).get("textbook")))
    if _is_literary:
        # 2026-11-22 用户口径：课文背后故事（textbook_story / ink_story）属于特殊题材，
        # **除非强制要求，否则不许使用**。判定为文学类后必须由物料显式声明
        # `force_textbook: true`（或临时 ALLOW_TEXTBOOK=1），否则直接拦死——
        # 防止默认路由/物料里残留 textbook 字段就把整条片带进课文模板。
        assert_textbook_authorized(True, ctx.material, _tpl)
        print(f"[creator_take] SKIP 文学类模板（{_tpl or 'textbook/ink'}）不需要新闻锐评，门禁不适用（已显式强制启用）")
    else:
        _take_errors = check_creator_takes(
            [sc.model_dump() for sc in payload.scenes], payload.fact_summary_150)
        print(f"[creator_take] {summarize([sc.model_dump() for sc in payload.scenes], payload.fact_summary_150)}")
        if _take_errors:
            # 历史 run 重渲染用逃生阀（旧物料没有 creator_take），新内容一律不许绕过
            if os.environ.get("ALLOW_LEGACY_NO_TAKE") == "1":
                print("[creator_take] WARN 门禁被 ALLOW_LEGACY_NO_TAKE=1 绕过（仅限历史 run 重渲染）")
            else:
                raise ExecutorError("creator_take 门禁未通过（分镜缺二次创作或在复述原文）:\n  "
                                    + "\n  ".join(_take_errors))

    # T4.5：音色/语速一律来自 pipeline.yaml 的 tts.* 槽位（缺配置即 MissingConfig）
    # edu（课文故事）复用「讲述向」的 tts.news 槽位：M1 不新增 required_env 键，
    # 否则缺 TTS_VOICE_EDU 会让整条链路 fail-fast；M2 若需要专属讲述音色再开 tts.edu。
    slot = "tts.tech" if str(draft.get("target_account_type") or "tech") == "tech" else "tts.news"
    aligner = TTSAligner.from_slot(slot)

    props_dir = ctx.run_root / "props"
    props_dir.mkdir(parents=True, exist_ok=True)
    public_prefix = f"runs/{ctx.run_id}"

    link = RENDERER / "public" / "runs" / ctx.run_id
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(props_dir, target_is_directory=True)

    aligned = asyncio.run(aligner.align_draft(
        payload, ctx.task_id, str(RENDERER / "public"),
        props_dir=str(props_dir), public_prefix=public_prefix))

    props_path = props_dir / "aligned_render.json"
    props_data = aligned.model_dump()
    # T4.4：封面三卡取哪几镜由 pipeline.yaml 的 cover.card_source 声明
    from src.pipeline.contracts import load_pipeline
    props_data["cover_card_source"] = (
        (load_pipeline().get("cover") or {}).get("card_source") or [0, 1, 3])
    # 新模板（tech_story）需要按素材库选 open/bg/bgm；老模板不注入，行为不变
    _comp = _composition_for(props_data)
    # 注意：模板解析（templates_manager）在素材注入之前就把 template_id 定成了 tech_story（默认模板），
    # 所以只看 _comp 会把课文物料判成科技类，抽到 assets/tech 的素材。以物料为准再判一次。
    _man = ctx.material or {}
    _is_literary = (bool(_man.get("textbook"))
                    or str(_man.get("content_type") or "") in ("textbook_story", "ink_story")
                    or str(props_data.get("template_id") or "") in ("textbook_story", "ink_story"))
    if _comp == "TechStoryVideo" and not _is_literary:
        _attach_assets(ctx, props_data)
    elif _comp in ("InkStoryVideo", "TextbookStoryVideo") or _is_literary:
        # 2026-11-19（方案 C）：水墨本 / 课文本都走 ink_story 库（6 槽位）。
        # 课文本把 assets.bg 当水墨底、assets.tex 当纸纹；缺素材时渲染层回落纯纸色。
        _attach_assets(ctx, props_data, library="ink_story")

    # F-2026-11-12 M2：课文物料把 textbook/compliance 透传给渲染层 ——
    # 竖排原文取 full_text，片尾固定标注教材版本（spec F7）。科技物料不带这两块，行为不变。
    if str((ctx.material or {}).get("content_type") or "") == "textbook_story":
        props_data["content_type"] = "textbook_story"
        props_data["textbook"] = (ctx.material or {}).get("textbook") or {}
        props_data["compliance"] = (ctx.material or {}).get("compliance") or {}
    _write_json(props_path, props_data)
    return props_path


def exec_align_tts(ctx: StepContext) -> dict[str, str]:
    """builtin.align_tts：只做 TTS 对齐 + props 落盘，竖版/横版两路共用。

    拆出这一步是并行渲染的前提：横版读 props，不读竖版成片。
    """
    draft = _load_draft(ctx)
    return {"props": str(_prepare_props(ctx, draft))}



#: 封面抽帧位置（2026-11-18）：模板的标题从第 18–48 帧淡入，第 0 帧几乎无字，
#: 抽第 0 帧会得到「文字还没出现」的封面。默认 54（1.8s，标题+副标题都已就位），
#: 可用 COVER_FRAME 覆盖（老模板/课文模板若淡入更久，调大即可）。
DEFAULT_COVER_FRAME = 54


def _cover_frame_no() -> int:
    raw = os.environ.get("COVER_FRAME")
    try:
        return max(0, int(raw)) if raw else DEFAULT_COVER_FRAME
    except ValueError:
        return DEFAULT_COVER_FRAME


def _extract_first_frame(video: Path | str, out_png: Path | str, *, expect_size: tuple[int, int] | None = None) -> Path:
    """封面图 = 视频第 COVER_FRAME 帧（默认 54，SPEC-20261117 §八 8.2 #3/#4；原为第 0 帧）。

    旧口径「单独渲 CoverStill 海报」自 2026-11-17 起作废：封面帧与正文天然两张皮，
    用户指令改为「封面直接用视频首帧」。

    A9 失败态：抽帧失败（视频损坏 / 0 帧 / 空文件）**必须显式报错并保留现场**，
    不回落旧 CoverStill、不产出空封面 → 上层 publish 拿不到文件自然不发。
    """
    video, out_png = Path(video), Path(out_png)
    if not video.exists() or video.stat().st_size == 0:
        raise ExecutorError(f"COVER_FRAME_EXTRACT_FAILED: 视频不存在或为空: {video}")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    frame_no = _cover_frame_no()
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", f"select=eq(n\\,{frame_no})", "-frames:v", "1", str(out_png)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out_png.exists() or out_png.stat().st_size == 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise ExecutorError(f"COVER_FRAME_EXTRACT_FAILED: 抽第 {frame_no} 帧失败 ({video.name}) | "
                            f"rc={proc.returncode} | {' / '.join(tail)}")
    if expect_size:
        got = _probe_size(out_png)
        if got != expect_size:
            raise ExecutorError(f"COVER_FRAME_EXTRACT_FAILED: 封面尺寸 {got} ≠ 期望 {expect_size}（{out_png.name}）")
    return out_png


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        w, h = out.split(",")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return (0, 0)


#: 片头封面停留秒数（2026-11-18 用户要求：封面帧直接贴到成片最前面播放 0.5s）
COVER_LEAD_SECONDS = 0.5


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _prepend_cover(video: Path, cover: Path, seconds: float = COVER_LEAD_SECONDS) -> Path:
    """把封面图当成片头，静止播放 `seconds` 秒，再接正片。

    为什么这么做（2026-11-18）：抖音上传页的「设置封面」控件一直没能稳定用起来，
    平台默认取首帧。于是让**首帧就是我们要的封面**——封面帧进片头，平台自动用它，
    不再依赖任何平台的封面上传；视频号同理（原话「后面不需要单独做封面上传」）。

    实现要点：
      · 只重编码一次（libx264 crf18），音频用 adelay 整体后移 0.5s 保持口播同步；
      · 正片留存一份 `video_body.mp4`，出问题能直接比对是封面段还是正片段；
      · 任何一步失败显式报错并恢复原 video.mp4（宁可没有片头封面，也不能产出半成品）。
    """
    video, cover = Path(video), Path(cover)
    if not cover.exists() or cover.stat().st_size == 0:
        raise ExecutorError(f"COVER_PREPEND_FAILED: 封面不存在: {cover}")
    if seconds <= 0:
        return video

    width, height = _probe_size(video)
    if not width or not height:
        raise ExecutorError(f"COVER_PREPEND_FAILED: 探不到成片尺寸: {video.name}")
    total = _probe_duration(video)
    if total <= 0:
        raise ExecutorError(f"COVER_PREPEND_FAILED: 探不到成片时长: {video.name}")

    # 2026-11-20：正片备份按片名派生。原来固定叫 video_body.mp4，横版
    # （video_16_9.mp4）贴片头时会命中竖版那份备份并把它 unlink 掉 ——
    # 竖版正片丢失，两版互相覆盖。
    body = video.with_name(f"{video.stem}_body.mp4")
    tmp = video.with_name(f".{video.stem}_with_cover.mp4")
    if body.exists():
        body.unlink()
    video.replace(body)

    ms = int(round(seconds * 1000))
    target = total + seconds
    filt = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v0];"
        f"[1:v]scale={width}:{height},setsar=1,fps=30,format=yuv420p[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[v];"
        f"[1:a]adelay={ms}:all=1[a]"
    )
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-loop", "1", "-t", f"{seconds}", "-i", str(cover), "-i", str(body),
         "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-t", f"{target:.3f}", str(tmp)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        if tmp.exists():
            tmp.unlink()
        body.replace(video)                       # 恢复原片，不产半成品
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise ExecutorError(f"COVER_PREPEND_FAILED: 封面片头合成失败（{video.name}） | "
                            f"rc={proc.returncode} | {' / '.join(tail)}")
    tmp.replace(video)
    got = _probe_duration(video)
    if abs(got - target) > 0.15:
        raise ExecutorError(f"COVER_PREPEND_FAILED: 时长异常 {got:.2f}s（期望 {target:.2f}s）")
    return video


def _make_upload_cover(video: Path, out: Path, *, cover_ref: Path) -> Path:
    """从成片前 0.5s 抽一帧作为上传封面，并自证「这段确实是静止封面」。"""
    from src.pipeline.cover_lead import CoverLeadError, make_upload_cover

    try:
        info = make_upload_cover(video, out, cover_ref=cover_ref)
    except CoverLeadError as exc:
        raise ExecutorError(f"COVER_UPLOAD_FRAME_FAILED: {exc}") from exc
    print(f"[cover] 上传封面帧 = 成片 {info['at_sec']}s（前 0.5s 内）"
          f" static_spread={info['static_spread']} dHash(vs cover)={info['dhash_vs_cover']}")
    return Path(str(info["path"]))


def _render_vertical_only(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """竖版渲染（只渲染，不碰 TTS —— props 由 align_tts 产出）。"""
    props = ctx.artifacts.get("props") or str(ctx.run_root / "props" / "aligned_render.json")
    if not Path(props).exists():
        raise ExecutorError(f"竖版 props 不存在: {props}（align_tts 未产出）")

    video = ctx.out("video.mp4")
    cover = ctx.out("cover.png")
    try:
        _props_data = json.loads(Path(props).read_text(encoding="utf-8"))
    except Exception as exc:  # props 坏了必须显式失败，不静默回落老模板
        raise ExecutorError(f"props 解析失败: {props}: {exc}") from exc
    composition = _composition_for(_props_data)
    _remotion("render", "src/index.ts", composition, str(video),
              f"--props={Path(props).resolve()}", "--codec=h264", "--crf=18",
              "--pixel-format=yuv420p")
    # 封面 = 成片截帧做底图 + tech_story 语汇渲染文案（§十一 R13~R18）；
    # 不再渲 CoverStill 海报，也不再直接拿第 0 帧（描边大字 OCR 读不出）。
    from src.pipeline.cover_compose import compose_cover
    compose_cover(video, cover, props=_props_data, run_id=ctx.run_id, aspect="portrait")
    # 封面帧贴到片头播 0.5s（§十二 2026-11-18）：首帧即封面，抖音/视频号都不再需要
    # 单独上传封面。竖版一条改动，两个平台同时生效。
    _prepend_cover(video, cover)
    _assert_has_audio(video)   # 无声片零容忍
    # 2026-11-20 用户口径：需要单独上传封面的平台，用成片前 0.5s 内的一帧当封面。
    # 前 0.5s 是封面片头（静止），所以抽哪一帧都一样；抽完自证静止性，不是就报错。
    up = _make_upload_cover(video, ctx.run_root / "cover_upload.png", cover_ref=cover)
    return {"video": str(video), "cover": str(cover), "cover_upload": str(up)}


def _already_leads_with_cover(video: Path, cover: Path, threshold: float = 8.0) -> bool:
    """首帧是否已经是封面（幂等判据：续跑时不要重复贴片头）。

    用 PIL 做 32×32 灰度平均差，**不碰 landscape_guard 的帧指标实现**：
    guard 里的 `_frame_diff` 走 rawvideo 通道，吃 PNG 会报 Invalid data，
    这里独立实现，避免为了幂等去改出厂校验的代码路径。
    """
    video, cover = Path(video), Path(cover)
    if not video.exists() or not cover.exists():
        return False
    import tempfile

    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:  # noqa: BLE001 — 没装 PIL 就保守当作「没贴过」
        return False

    first = Path(tempfile.mkdtemp()) / "first.png"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", str(first)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not first.exists() or first.stat().st_size == 0:
        return False
    try:
        with Image.open(first) as a, Image.open(cover) as b:
            ga = a.convert("L").resize((32, 32), Image.LANCZOS)
            gb = b.convert("L").resize((32, 32), Image.LANCZOS)
            diff = ImageStat.Stat(ImageChops.difference(ga, gb)).mean[0]
        return float(diff) <= threshold
    except Exception:  # noqa: BLE001
        return False


def _render_landscape(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """横版：复用 scripts/render_landscape.py（内置 landscape_guard 出厂校验）。

    T4.1：直接渲染到 run_root，产物名已是规范名 `video_16_9.mp4` / `cover_16_9.png`。
    """
    from scripts.render_landscape import render_landscape

    props = ctx.artifacts.get("props") or str(ctx.run_root / "props" / "aligned_render.json")
    if not Path(props).exists():
        raise ExecutorError(f"横版 props 不存在: {props}（align_tts 未产出）")

    textbook = str((ctx.material or {}).get("content_type") or "") == "textbook_story"
    # SPEC-20261117 §九 R6：科技类横版走**同一个 tech_story 模板**（TechStoryLandscapeVideo），
    # 不再用 BilibiliLandscapeVideo 这套独立版式；课文本链路保持不变。
    try:
        _pd = json.loads(Path(props).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ExecutorError(f"横版 props 解析失败: {props}: {exc}") from exc
    tech_landscape = (not textbook) and _composition_for(_pd) == "TechStoryVideo"

    # 2026-11-19 修复回归：efa3a98（封面改成成片截帧）把这段横版副本替换逻辑删掉了，
    # 于是 11-18 起横版又回去「竖版素材中心裁成 16:9」（1080x1920 裁完只剩 1080x608，
    # 上下各丢 34%）。这里恢复并把 mid/end 一并纳入：开场/背景/幕间/片尾都优先用原生 16:9 副本。
    props_for_render = props
    if tech_landscape:
        from src.assets_manager import with_landscape_assets
        _lp, _lrep = with_landscape_assets(_pd)
        _used = sorted((_lrep.get("used") or {}).keys())
        print(f"[assets] aspect=landscape 原生 16:9 副本: {_used or '（无）'} "
              f"回落竖版裁切: {_lrep.get('fallback') or '（无）'}")
        if _used:
            land_props = Path(ctx.run_root) / "props" / "aligned_render_landscape.json"
            land_props.write_text(json.dumps(_lp, ensure_ascii=False, indent=2), encoding="utf-8")
            props_for_render = str(land_props)
    def _landscape_cover(video_path: Path, cover_path: Path) -> Path:
        from src.pipeline.cover_compose import compose_cover
        return compose_cover(video_path, cover_path, props=_pd, run_id=ctx.run_id, aspect="landscape")

    info = render_landscape(ctx.task_id, props_json=props_for_render, out_dir=str(ctx.run_root),
                            expect_cover_text=str(draft.get("cover_main_text") or ""),
                            cover_composer=_landscape_cover if tech_landscape else None,
                            video_composition=("TextbookLandscapeVideo" if textbook else
                                               "TechStoryLandscapeVideo" if tech_landscape else None),
                            cover_composition="TextbookLandscapeCoverStill" if textbook else None)
    video = Path(info.get("video_path_generated") or ctx.run_root / "video_16_9.mp4")
    cover = Path(info.get("cover_path") or ctx.run_root / "cover_16_9.png")

    # 封面帧贴片头 0.5s（2026-11-18 用户点名要横版也有；竖版早已有）。
    # 位置刻意放在 render_landscape 返回之后：landscape_guard 的出厂校验（覆盖率/边缘/
    # cover_frame_diff）仍按**纯正片**算，不会被这 0.5s 带偏。
    # 幂等：首帧已经≈封面就跳过（避免 resume 时二次贴片导致 +1.0s）。
    if _already_leads_with_cover(video, cover):
        print(f"[publish] 横版首帧已是封面，跳过贴片头：{video.name}")
    else:
        _prepend_cover(video, cover)
        print(f"[publish] 横版已贴封面片头 {COVER_LEAD_SECONDS}s：{video.name} "
              f"{_probe_duration(video):.3f}s")
    _assert_has_audio(video)   # 横版同样不许出无声片
    # 2026-11-20：横版原来没有封面片头 → B 站「必须有封面」抽到的是正片首帧，
    # 与竖版封面完全不是一张图。这里补 0.5s 封面片头，让「前 0.5s 都是封面帧」
    # 在横版也成立，上传封面才能统一取这一帧。
    _prepend_cover(video, cover)
    up_land = _make_upload_cover(video, ctx.run_root / "cover_upload_16_9.png", cover_ref=cover)
    # R9：横版也打一行，方便回归时与竖版一眼对齐（素材来自同一份 Selection：props 里已注入）
    _a = (_pd.get("asset_binding") or {})
    _op, _bg = (_a.get("open") or {}), (_a.get("bg") or {})
    _mid, _end = (_a.get("mid") or {}), (_a.get("end") or {})
    print(f"[assets] aspect=landscape 档位={_a.get('domain') or '未判定'} "
          f"封面形态={_a.get('cover_mode') or 'none'} 封面=首帧+片头{COVER_LEAD_SECONDS}s "
          f"open={_op.get('id') or '（无）'} bg={_bg.get('id') or '（无）'} "
          f"mid={_mid.get('id') or '（无）'} end={_end.get('id') or '（无）'} "
          f"模板={'TechStoryLandscapeVideo' if tech_landscape else ('TextbookLandscapeVideo' if textbook else '旧横版')}")
    return {"video_16_9": str(video), "cover_16_9": str(cover),
            "cover_upload_16_9": str(up_land)}


def _prune_dangling_public_links() -> list[str]:
    """删掉 `renderer/public/runs/` 下指向已删 run 的失效软链。

    实测（2026-11-11 17:10）：Remotion bundler 会把整个 `public/` 递归
    `copyDir → realpath`，任何一条悬空软链都让它 `ENOENT` 退出 —— 竖版横版同时失败
    （`EXIT=64`），而报错指向 realpath，根本看不出是软链。清理必须发生在渲染之前，
    且要覆盖**所有** run_id 的残留，不只是本次的。
    """
    runs_dir = RENDERER / "public" / "runs"
    if not runs_dir.is_dir():
        return []
    pruned: list[str] = []
    for child in sorted(runs_dir.iterdir()):
        if child.is_symlink() and not child.exists():
            child.unlink()
            pruned.append(child.name)
    if pruned:
        print(f"  [render] 清理失效软链 {len(pruned)} 条: {', '.join(pruned)}")
    return pruned


def exec_render(ctx: StepContext) -> dict[str, str]:
    """builtin.render：按 params.aspect 分派竖版 / 横版（两者都只读 props）。"""
    _prune_dangling_public_links()
    draft = _load_draft(ctx)
    aspect = str((ctx.params or {}).get("aspect") or "vertical")
    if aspect == "landscape":
        return _render_landscape(ctx, draft)
    return _render_vertical_only(ctx, draft)


def exec_approval(ctx: StepContext) -> dict[str, str]:
    """gate.approval：走适配层（feishu / cli / auto），runner 不再关心渠道细节。"""
    from src.approval import request_approval

    params = ctx.step.get("params") or {}
    mode = str(params.get("mode") or "auto")
    message = str(params.get("message") or "请确认成片")
    try:
        result = request_approval(ctx.run_id, ctx.artifacts, message, mode=mode)
    except Exception as exc:  # noqa: BLE001 — 渠道不可用必须变成步失败，不能裸抛到 runner
        raise ExecutorError(f"审批渠道不可用（mode={mode}）：{exc}") from exc

    out = _write_json(ctx.out("approval.json"), result)
    if result.get("decision") != "approve":
        raise ExecutorError(
            f"审批未通过（{result.get('decision')}，渠道={result.get('mode')}，"
            f"依据={result.get('decided_by')}）：{result.get('reason') or ''}")
    return {"approval": str(out)}


def _pick_media(ctx: StepContext, aspect: str = "vertical") -> tuple[Path, Path]:
    """按 aspect 取成片与封面（发布统一用竖版，横版留给需要 16:9 的平台）。"""
    if aspect == "landscape":
        return (Path(ctx.artifacts.get("video_16_9") or ctx.run_root / "video_16_9.mp4"),
                Path(ctx.artifacts.get("cover_16_9") or ctx.run_root / "cover_16_9.png"))
    return (Path(ctx.artifacts.get("video") or ctx.run_root / "video.mp4"),
            Path(ctx.artifacts.get("cover") or ctx.run_root / "cover.png"))


def _release_slot(db, dedupe_key: str) -> None:
    """发布失败/仅预检时释放台账位，否则这条内容永远发不出去（被自己的 claim 挡住）。"""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM send_ledger WHERE dedupe_key = ?", (dedupe_key,))
        conn.commit()


def _invoke_provider(platform: str, account: dict[str, Any], video: Path, cover: Path,
                     draft: dict[str, Any], dry_run: bool, *,
                     run_root: Path | None = None,
                     opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """按平台调 provider。三家的方法名不一致，这里收口，不散落到调用方。

    文案：统一走 `src/utils/video_desc`（三个平台 CLI 早就在用，唯一权威）。
    之前这里用 narration[:180]，与 CLI 拼出来的简介不是一回事，同一条视频
    走 pipeline 和走 CLI 发出去的文案不一样（2026-11-11 实测 158 / 213 字）。
    声明参数：从 accounts.yaml 的账号配置读，不再硬编码（否则卡片说的和
    实际勾选的不一致）。
    """
    from src.utils.video_desc import build_desc

    # 2026-11-20 用户口径：需要上传封面的平台，用「成片前 0.5s 内的一帧」。
    # 渲染时已按这条规则抽好 cover_upload*.png（前 0.5s 是静止封面片头），
    # 这里统一把 cover 指过去；没有就退回源图，绝不现场把正片画面截成封面。
    try:
        from src.utils.upload_cover import ensure_upload_cover as _euc
        _lead, _why = _euc(video, None, allow_extract=False)
        if _lead and Path(_lead).name.startswith("cover_upload"):
            print(f"[cover] {platform} 使用上传封面={Path(_lead).name}（{_why}）")
            cover = Path(_lead)
    except Exception as exc:  # noqa: BLE001 — 取封面失败不应打断发布
        print(f"[cover] 取前 0.5s 封面帧失败（沿用 {Path(cover).name}）：{type(exc).__name__}: {exc}")

    opts = opts or {}
    title = str(draft.get("title") or "")
    tags = [str(t).strip() for t in (draft.get("tags") or []) if str(t).strip()]
    topics = tags or (["科技"] if not draft.get("resource_keyword")
                      else [str(draft.get("resource_keyword")).strip()])

    declare_original = (bool(opts["declare_original"]) if "declare_original" in opts
                        else bool(account.get("declare_original", False)))
    declare_aigc = (bool(opts["declare_aigc"]) if "declare_aigc" in opts
                    else bool(account.get("declare_aigc", False)))

    if platform == "channels":
        from src.providers.channels_cdp import ChannelsCDPProvider
        # 视频号 provider 自己拼 #标签，正文里不要重复
        body = build_desc(draft, platform="channels", run_root=run_root, include_tags=False)
        extra: dict[str, Any] = {}
        if opts.get("stop_before_publish"):
            extra["stop_before_publish"] = True
        if opts.get("allow_missing_flags"):
            extra["allow_missing_flags"] = True
        # 浏览器形态：显式步骤参数 > 账号配置(accounts.yaml: headless) > provider 默认(有头)。
        # 不传 None 给 provider 表示「没显式指定」，让它去读账号标记 —— 否则账号里的
        # headless 永远被这里写死，配置形同虚设（2026-11-15 修）。
        ch_headless = opts["headless"] if "headless" in opts else None
        return ChannelsCDPProvider(account).publish_live(
            video_path=str(video), cover_path=str(cover), title=title,
            content_text=body, topics=topics,
            declare_original=declare_original, declare_aigc=declare_aigc,
            dry_run=dry_run, headless=ch_headless, **extra)
    if platform == "douyin":
        from src.providers.douyin import DouyinProvider
        body = build_desc(draft, platform="douyin", run_root=run_root, include_tags=False)
        prov = DouyinProvider(account)
        # 抖音识别无头（扫码/短信验证必失败）→ 默认有头；账号配置或步骤参数可覆盖
        if "headless" in opts:
            prov.headless_publish = bool(opts.get("headless"))
        return prov.save_draft(
            video_path=str(video), cover_path=str(cover), title=title,
            topics=topics, content_text=body,
            stop_before_publish=bool(opts.get("stop_before_publish")))
    if platform == "bilibili":
        from src.providers.bilibili import BilibiliProvider
        body = build_desc(draft, platform="bilibili", run_root=run_root, include_tags=True)
        return BilibiliProvider(account).save_draft(
            video_path=str(video), cover_path=str(cover), title=title,
            topics=topics, content_text=body)
    raise ExecutorError(f"未知平台: {platform}（支持 douyin | bilibili | channels）")


def send_publish_receipt(report: dict[str, Any], title: str = "") -> str:
    """发发布回执卡（唯一实现，pipeline 与各 CLI 共用）。失败只告警，不影响发布结论。"""
    from src.feishu.approval_service import FeishuApprovalService
    from src.feishu.card_builder import FeishuCardBuilder, publish_receipt_text

    chat = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not chat:
        print("  ! 发布回执跳过：未配置 FEISHU_APPROVAL_CHAT_ID")
        return ""
    id_type = "chat_id" if chat.startswith("oc_") else "open_id"
    card = FeishuCardBuilder.build_publish_receipt(report, title)
    msg_id = FeishuApprovalService().send_approval_card(
        receive_id=chat, receive_id_type=id_type, card_content=card)
    print(f"  ✓ 发布回执已发（msg_id={msg_id}）：{publish_receipt_text(report, title)}")
    return str(msg_id or "")


def _adopt_late_decision(ctx: StepContext, ap_path: Path) -> None:
    """卡片裁决晚于审批步时，把 run 级裁决文件补记进 approval.json。

    场景（cyber77-1 实测）：审批卡等满超时被判 reject → run 卡在 BLOCKED；人过一会
    才点「✅ 发布」。点卡片写的是 data/approvals/<run_id>.json，没人回填
    approval.json，于是 resume 又被门禁2 拦死，只能人工改状态库。

    判定：裁决文件比 approval.json 新（mtime）且 decision 合法 → 以它为准。
    只做「补记」，不伪造来源：decided_by 照抄裁决文件里的 by（card:...）。
    """
    try:
        from src.approval.feishu import _decision_path

        dp = _decision_path(ctx.run_id)
        if not dp.exists():
            return
        if ap_path.exists() and dp.stat().st_mtime <= ap_path.stat().st_mtime:
            return
        data = json.loads(dp.read_text(encoding="utf-8"))
        decision = str(data.get("decision") or "").lower()
        if decision not in ("approve", "reject"):
            return
        rec = {
            "decision": decision,
            "decided_by": f"feishu_file:{data.get('by') or '?'}",
            "feishu_msg_id": data.get("msg_id") or "",
            "evidence_msg_id": data.get("evidence_msg_id") or "",
            "mode": "feishu",
            "reason": "补记：卡片裁决晚于审批步（审批步曾超时判 reject）",
            "adopted_from": str(dp),
        }
        ap_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  [approval] 采纳晚到的卡片裁决：{decision}（by={rec['decided_by']}）")
    except Exception as exc:  # noqa: BLE001 — 补记失败不该让发布推进变成异常
        print(f"  [approval] 晚到裁决补记跳过：{exc}")


def exec_publish(ctx: StepContext) -> dict[str, str]:
    """builtin.publish：v2 发布方案 deliver→approval→publish，**三重门禁，默认关闭**。

    门禁1 配置：publish.enabled=false（默认）或 platforms 为空 → 直接跳过
    门禁2 审批：approval.json 的 decision 必须为 approve，否则拦
    门禁3 台账：send_ledger 的 claim_send_slot 抢到才发（同内容同账号不重发）
    """
    from src.db import PipelineDB
    from src.feishu.card_builder import publish_receipt_text
    from src.utils.accounts import aspect_for_platform, for_platform

    # 配置项后置：顶层 `publish:` 段 + 步骤 params（params 优先），都不写死默认值
    from src.pipeline.contracts import load_pipeline
    _pub_cfg = (load_pipeline() or {}).get("publish") or {}
    params = {**_pub_cfg, **(ctx.step.get("params") or {})}
    report: dict[str, Any] = {"mode": "publish", "published": [], "skipped": [], "failed": []}

    def _out() -> dict[str, str]:
        return {"publish": str(_write_json(ctx.out("publish.json"), report))}

    # ---- 门禁1：配置（默认关闭）----
    if not bool(params.get("enabled", False)):
        report["skipped"].append({"gate": "enabled", "reason": "publish.enabled=false（默认关闭）"})
        return _out()
    platforms = [str(x).lower() for x in (params.get("platforms") or [])]
    if not platforms:
        report["skipped"].append({"gate": "platforms", "reason": "platforms 为空，未指定发布平台"})
        return _out()

    # ---- 门禁2：审批 ----
    ap_path = ctx.run_root / "approval.json"
    _adopt_late_decision(ctx, ap_path)   # 卡片点击晚于审批步 → 按 mtime 补记
    decision = ""
    if ap_path.exists():
        try:
            decision = str(json.loads(ap_path.read_text(encoding="utf-8")).get("decision") or "")
        except json.JSONDecodeError as exc:
            raise ExecutorError(f"审批文件损坏，拒绝发布: {exc}") from exc
    if decision != "approve":
        raise ExecutorError(
            f"发布拦截（门禁2 审批）：decision={decision or '缺失'}，必须 approve 才能发布")

    draft = _load_draft(ctx)
    dry_run = bool(params.get("dry_run", True))
    # 方向按平台取（抖音竖版 / B站横版 / 视频号竖版）。params.aspect 只作为显式覆盖，
    # 用于「只发单平台」的手工场景；三平台一起发时各平台用各自的方向。
    forced_aspect = str(params.get("aspect") or "").strip()

    db = PipelineDB(str(ctx.db_path))
    from src.publish_ledger import PublishBlocked, check_and_claim, record_outcome

    # 点完按钮到发布结束有 1~3 分钟空窗 —— 立刻把审批卡原地改成「发布中」，
    # 审批人不用盯着聊天等消息。
    from src.approval.feishu import update_card_status

    _card_title = str(draft.get("title") or "")
    try:
        _by = str(json.loads(ap_path.read_text(encoding="utf-8")).get("decided_by") or "approval")
    except Exception:  # noqa: BLE001
        _by = "approval"
    update_card_status(ctx.run_id, f"⏳ 发布中 · {_card_title}", "blue",
                       [f"已批准（{_by}），正在发布到 {'、'.join(platforms)} …",
                        "完成后本卡会更新为结果，无需再发消息"])

    # ---- 门禁2.5：前置登录态预检（T2，2026-11-15）----
    # 抖音 CDP / 视频号常驻 Chrome / B站 cookie 任一不可用 → **不进发布循环、不占台账、
    # 不发任何内容**，直接发告警卡（含修复命令）并 exit≠0。
    # 过去的毛病是「跑到第 N 步才炸」：一半平台已发出去、另一半静默失败。
    if not dry_run:
        from src.pipeline.preflight import probe_all, send_alert

        probes = probe_all(platforms)
        for _r in probes:
            print(f"      · preflight {_r['platform']}: "
                  f"{'ok' if _r['ok'] else _r['code']} — {_r['detail']}")
        fails = [r for r in probes if not r["ok"]]
        if fails:
            for f in fails:
                report["failed"].append({"platform": f["platform"], "gate": "preflight",
                                         "error": f"{f['code']}: {f['detail']}",
                                         "fix": f.get("fix") or ""})
            report["preflight"] = probes
            report["receipt"] = ("🚫 前置预检未通过：" +
                                 "、".join(f["platform"] for f in fails) + "（未发布）")
            try:
                send_alert(fails, ctx.run_id, _card_title)
            except Exception as exc:  # noqa: BLE001 — 告警发不出去也要 exit≠0
                print(f"  ! 前置告警卡发送失败：{type(exc).__name__}: {exc}")
            _out()
            update_card_status(
                ctx.run_id, f"🚫 前置预检未通过 · {_card_title}", "red",
                [report["receipt"]] + [f"- ❌ {f['platform']}：{f['code']}" for f in fails])
            raise ExecutorError("发布前置预检未通过: " +
                                ", ".join(f["platform"] for f in fails))

    for plat in platforms:
        # 账号按「平台 + 账号类型」双重解析：edu 课文故事与 tech 速报各走各的号，
        # 杜绝「加了教育号，科技内容被发到教育号」的串号（F-2026-11-12 §11.3 缺口4）。
        # 该平台没有对应类型的启用账号 → AccountConfigError（fail-closed，不静默降级）。
        account = for_platform(plat, str(draft.get("target_account_type") or "tech"))
        acct_id = str(account.get("id") or "")

        # 每个平台用自己的成片方向：竖版平台发 video.mp4，B站发 video_16_9.mp4
        aspect = forced_aspect or aspect_for_platform(plat)
        video, cover = _pick_media(ctx, aspect)
        # 方向与成片必须写进 publish.json（回执/明细/返工全靠它），
        # 2026-11-14 之前明细里没有 aspect，B站连发三条竖屏没人能从台账看出来。
        _aspect_meta = {"aspect": aspect, "video": video.name, "cover": cover.name,
                        "source": "params.aspect(强制)" if forced_aspect else "PLATFORM_ASPECT"}
        print(f"      · {plat}: aspect={aspect} 成片={video.name} 封面={cover.name}"
              f"（来源 {_aspect_meta['source']}）")
        if not video.exists():
            report["failed"].append({"platform": plat, **_aspect_meta,
                                     "error": f"缺 {aspect} 成片: {video.name}"})
            continue
        if not cover.exists():
            report["failed"].append({"platform": plat, **_aspect_meta,
                                     "error": f"缺 {aspect} 封面: {cover.name}"})
            continue

        # ---- 预检（dry_run）：不碰台账、不真发 ----
        # 顺序很重要：dry_run 必须排在「门禁3 台账」**之前**，否则「已发过的内容」做预检
        # 会被台账拦成 gate=ledger，看起来像预检失败（2026-11-15 踩过）。
        if dry_run:
            report["skipped"].append({"platform": plat, "gate": "dry_run", **_aspect_meta,
                                      "reason": "dry_run=true，只预检不真发"})
            continue

        # ---- 门禁3：平台发布台账（与飞书 send_ledger 分开，fail-closed）----
        try:
            key, _info = check_and_claim(plat, acct_id, video, str(draft.get("title") or ""))
        except PublishBlocked as exc:
            report["skipped"].append({"platform": plat, "gate": "ledger", "reason": str(exc)})
            continue

        try:
            res = _invoke_provider(plat, account, video, cover, draft, dry_run=False,
                                   run_root=ctx.run_root,
                                   opts={"headless": bool(params.get("headless", False)),
                                         "stop_before_publish": bool(
                                             params.get("stop_before_publish", False))})
        except Exception as exc:  # noqa: BLE001 — 结果未知，禁止自动重发
            state = record_outcome(key, False, status="SUBMIT_UNKNOWN",
                                   reason=f"{type(exc).__name__}: {exc}")
            report["failed"].append({"platform": plat, "ledger": state, **_aspect_meta,
                                     "error": f"{type(exc).__name__}: {exc}"})
            continue

        if res.get("success"):
            # 只认平台真的给的 id；**不许用 `${plat}_${task_id}` 编一个**，
            # 那会把「回读没证据」伪装成成功（ds-coding-plan-1 出现过 post_id 空的 published 行）
            msg_id = str(res.get("msg_id") or res.get("bvid") or res.get("draft_id")
                         or res.get("post_id") or "")
            state = record_outcome(key, True, external_id=msg_id,
                                   status=str(res.get("status") or ""))
            # 话题证据跟着一条结论走：declared（准备写的）/ landed（平台真识别成话题的）
            _topics_meta = {k: res[k] for k in ("topics_declared", "topics_landed", "topics_ok") if k in res}
            if "readback" in res:
                _topics_meta["readback_topic"] = bool((res.get("readback") or {}).get("topic"))
            report["published"].append({"platform": plat, "msg_id": msg_id or "(无外部 id)", "ledger": state,
                                        **_aspect_meta, **_topics_meta,
                                        "status": res.get("status") or res.get("distribution") or "ok"})
        else:
            # 失败码优先取 `error`（LOGIN_REQUIRED / CDP_UNAVAILABLE / PUBLISHED_NO_TITLE…），
            # 分类器靠它判「可重试」；`post_id` 有值说明作品其实已经出现（有作品无标题），
            # 必须挂到 unverified 行上，方便人工定位补标题。
            state = record_outcome(key, False,
                                   status=str(res.get("status") or res.get("error") or ""),
                                   external_id=str(res.get("post_id") or ""),
                                   reason=str(res.get("message") or res.get("error") or "unknown"))
            report["failed"].append({"platform": plat, "ledger": state, **_aspect_meta,
                                     "post_id": str(res.get("post_id") or ""),
                                     "error": res.get("message") or res.get("error") or "unknown"})

    # ---- 回执 / 明细 / 卡片：全部收敛到 src/publish_report（一个 run 一张卡）----
    # 这里不再自己写 publish.json / 自己发卡：单平台补发走同一函数，才能保证
    # 「三次发布 = 一张卡 = 3/3」，而不是 1/1 + 1/1 两张各说一半。
    from src.publish_report import merge_and_finalize as _merge_and_finalize

    _title = str(draft.get("title") or "")
    report = _merge_and_finalize(
        ctx.run_root, report["published"] + report["failed"] + report["skipped"], _title,
        update_card=not dry_run)
    if report["failed"]:
        # 汇总/卡片已经写完了，才抛失败：调用方按失败处理，但结论与卡片不会丢
        raise ExecutorError("发布失败: " + ", ".join(
            str(f.get("platform")) for f in report["failed"]))
    return {"publish": str(ctx.out("publish.json"))}


def exec_dispatch(ctx: StepContext) -> dict[str, str]:
    """builtin.dispatch：飞书投递 + send_ledger 幂等（D2）。

    · `DISPATCH_MODE=output_only` / `params.enabled=false` → 只出片，不发送
    · 否则：算 dedupe_key → claim_send_slot → 抢到才发 → 成功后 mark_sent 回写 feishu_msg_id
    """
    params = ctx.step.get("params") or {}
    mode = str(params.get("mode") or "publish")
    if not params.get("enabled", True) or mode == "output_only":
        return {"dispatch": str(_write_json(ctx.out("dispatch.json"),
                                            {"mode": mode, "sent": False, "reason": "output_only"}))}

    from src.db import PipelineDB
    from src.utils.normalize import content_hash as compute_content_hash

    db_path = Path(ctx.db_path or os.getenv("SQLITE_DB_PATH")
                   or PROJ / "data" / "pipeline_state.db")
    db = PipelineDB(str(db_path))

    draft_path = ctx.artifacts.get("draft_script") or str(ctx.out("draft_script.json"))
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))

    # 按 aspect 遍历：pipeline.yaml 的 reads 声明了两个成片，就必须两个都发。
    # dedupe 口径保持 {content_hash}|{video_sha256}|{account} 不变 ——
    # 横竖版视频哈希本就不同，天然区分版本；把 aspect 加进口径会让
    # 历史台账里的竖版行判成"新内容"从而重复发送（Dry Run 实测）。
    aspects = params.get("aspects") or ["vertical", "landscape"]
    aspect_files = {
        "vertical": ("video", "cover", "video.mp4", "cover.png"),
        "landscape": ("video_16_9", "cover_16_9", "video_16_9.mp4", "cover_16_9.png"),
    }

    c_hash = compute_content_hash(str(draft.get("title") or ""), str(draft.get("content_text") or ""))
    account = str((ctx.material or {}).get("account") or "feishu")
    chat_id = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    rec_type = "open_id" if chat_id.startswith("ou_") else "chat_id"

    svc = None
    results: list[dict[str, Any]] = []
    for aspect in aspects:
        spec = aspect_files.get(aspect)
        if not spec:
            results.append({"aspect": aspect, "sent": False, "reason": "未知 aspect"})
            continue
        vkey, ckey, vname, cname = spec
        video = Path(ctx.artifacts.get(vkey) or ctx.out(vname))
        cover = Path(ctx.artifacts.get(ckey) or ctx.out(cname))
        if not video.exists():
            results.append({"aspect": aspect, "sent": False, "reason": f"产物缺失 {vname}"})
            continue

        v_hash = sha256_file(video)
        dedupe_key = __import__("hashlib").sha256(
            f"{c_hash}|{v_hash}|{account}".encode()).hexdigest()

        if not db.claim_send_slot(dedupe_key, ctx.task_id, "feishu", chat_id, str(video)):
            results.append({"aspect": aspect, "sent": False, "already_sent": True,
                            "dedupe_key": dedupe_key})
            continue

        if svc is None:
            from src.feishu.approval_service import FeishuApprovalService
            svc = FeishuApprovalService()
        msg_id = svc.upload_and_send_video(
            receive_id=chat_id, receive_id_type=rec_type, video_path=str(video),
            content_hash=c_hash, task_id=ctx.task_id,
            cover_path=str(cover) if cover.exists() else None)
        if msg_id:
            db.mark_sent(dedupe_key, msg_id)      # 外部确认成功后才写台账
        results.append({"aspect": aspect, "sent": bool(msg_id), "already_sent": False,
                        "dedupe_key": dedupe_key, "feishu_msg_id": msg_id,
                        "file": video.name})

    sent_any = any(r.get("sent") for r in results)
    all_done = all(r.get("sent") or r.get("already_sent") for r in results)
    record = {
        "sent": sent_any,
        "already_sent": all_done and not sent_any,
        "aspects": results,
        # 兼容旧消费方：单 aspect 时顶层直接给出 msg_id
        "feishu_msg_id": next((r.get("feishu_msg_id") for r in results
                               if r.get("feishu_msg_id")), None),
    }
    return {"dispatch": str(_write_json(ctx.out("dispatch.json"), record))}


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 注册表

EXECUTORS: dict[str, Callable[[StepContext], dict[str, str]]] = {
    "builtin.intake": exec_intake,
    "builtin.check_material": exec_check_material,
    "llm.main": exec_expand_script,
    "builtin.align_tts": exec_align_tts,
    "builtin.render": exec_render,
    "gate.approval": exec_approval,
    "builtin.dispatch": exec_dispatch,
    "builtin.publish": exec_publish,
}


def resolve(step: dict[str, Any]) -> Callable[[StepContext], dict[str, str]] | None:
    """按 step 的 `run` / `uses` / `type` 找到执行体。"""
    key = step.get("run") or step.get("uses")
    if not key and step.get("type") == "gate":
        key = "gate.approval"
    return EXECUTORS.get(str(key)) if key else None


def run_step(step: dict[str, Any], ctx: StepContext) -> dict[str, str]:
    fn = resolve(step)
    if fn is None:
        raise ExecutorError(f"未接线的执行体: {step.get('run') or step.get('uses') or step.get('type')}")
    return fn(ctx)
