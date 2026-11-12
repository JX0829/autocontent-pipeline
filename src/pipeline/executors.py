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


# ---------------------------------------------------------------- 执行体

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
    from src.schemas import DraftScriptPayload

    man = ctx.material or {}
    news_text = "\n\n".join(x for x in [str(man.get("title") or ""), str(man.get("body") or "")] if x)
    if not news_text.strip():
        raise ExecutorError("物料没有可用于扩写的正文")

    # material 透传：content_type=textbook_story 时走课文分支（target_duration_sec 注入 + 镜数预算）
    data = pick_and_convert_hotspot(news_text, task_id=ctx.task_id, material=man)
    payload = DraftScriptPayload(**data)            # schema 校验（失败即抛，不兜底）
    out = _write_json(ctx.out("draft_script.json"), payload.model_dump())
    return {"draft_script": str(out)}


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

    # F-2026-11-12 M2：课文物料把 textbook/compliance 透传给渲染层 ——
    # 竖排原文取 full_text，片尾固定标注教材版本（spec F7）。科技物料不带这两块，行为不变。
    man = ctx.material or {}
    if str(man.get("content_type") or "") == "textbook_story":
        props_data["content_type"] = "textbook_story"
        props_data["textbook"] = man.get("textbook") or {}
        props_data["compliance"] = man.get("compliance") or {}
    _write_json(props_path, props_data)
    return props_path


def exec_align_tts(ctx: StepContext) -> dict[str, str]:
    """builtin.align_tts：只做 TTS 对齐 + props 落盘，竖版/横版两路共用。

    拆出这一步是并行渲染的前提：横版读 props，不读竖版成片。
    """
    draft = _load_draft(ctx)
    return {"props": str(_prepare_props(ctx, draft))}


def _render_vertical_only(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """竖版渲染（只渲染，不碰 TTS —— props 由 align_tts 产出）。"""
    props = ctx.artifacts.get("props") or str(ctx.run_root / "props" / "aligned_render.json")
    if not Path(props).exists():
        raise ExecutorError(f"竖版 props 不存在: {props}（align_tts 未产出）")

    video = ctx.out("video.mp4")
    cover = ctx.out("cover.png")
    # M2：课文故事走独立古风合成；科技物料仍走 GeekShortVideo（默认路径一字不改）
    textbook = str((ctx.material or {}).get("content_type") or "") == "textbook_story"
    composition = "TextbookStoryVideo" if textbook else "GeekShortVideo"
    cover_composition = "TextbookCoverStill" if textbook else "CoverStill"
    _remotion("render", "src/index.ts", composition, str(video),
              f"--props={Path(props).resolve()}", "--codec=h264", "--crf=18",
              "--pixel-format=yuv420p")
    _remotion("still", "src/index.ts", cover_composition, str(cover),
              f"--props={Path(props).resolve()}", "--image-format=png")
    _assert_has_audio(video)   # 无声片零容忍
    return {"video": str(video), "cover": str(cover)}


def _render_landscape(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """横版：复用 scripts/render_landscape.py（内置 landscape_guard 出厂校验）。

    T4.1：直接渲染到 run_root，产物名已是规范名 `video_16_9.mp4` / `cover_16_9.png`。
    """
    from scripts.render_landscape import render_landscape

    props = ctx.artifacts.get("props") or str(ctx.run_root / "props" / "aligned_render.json")
    if not Path(props).exists():
        raise ExecutorError(f"横版 props 不存在: {props}（align_tts 未产出）")

    textbook = str((ctx.material or {}).get("content_type") or "") == "textbook_story"
    info = render_landscape(ctx.task_id, props_json=props, out_dir=str(ctx.run_root),
                            expect_cover_text=str(draft.get("cover_main_text") or ""),
                            video_composition="TextbookLandscapeVideo" if textbook else None,
                            cover_composition="TextbookLandscapeCoverStill" if textbook else None)
    video = Path(info.get("video_path_generated") or ctx.run_root / "video_16_9.mp4")
    cover = Path(info.get("cover_path") or ctx.run_root / "cover_16_9.png")
    _assert_has_audio(video)   # 横版同样不许出无声片
    return {"video_16_9": str(video), "cover_16_9": str(cover)}


def exec_render(ctx: StepContext) -> dict[str, str]:
    """builtin.render：按 params.aspect 分派竖版 / 横版（两者都只读 props）。"""
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
                     draft: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """按平台调 provider。三家的方法名不一致，这里收口，不散落到调用方。"""
    title = str(draft.get("title") or "")
    scenes = draft.get("scenes") or []
    narration = "".join(str(s.get("segment_narration") or "") for s in scenes)
    body = narration[:180].strip() or str(draft.get("theme") or "")
    kw = str(draft.get("resource_keyword") or "").strip()
    topics = [kw] if kw else ["科技"]

    if platform == "channels":
        from src.providers.channels_cdp import ChannelsCDPProvider
        return ChannelsCDPProvider(account).publish_live(
            video_path=str(video), cover_path=str(cover), title=title,
            content_text=body, topics=topics,
            declare_original=True, declare_aigc=True,
            dry_run=dry_run, headless=True)
    if platform == "douyin":
        from src.providers.douyin import DouyinProvider
        return DouyinProvider(account).save_draft(
            video_path=str(video), cover_path=str(cover), title=title,
            topics=topics, content_text=body)
    if platform == "bilibili":
        from src.providers.bilibili import BilibiliProvider
        return BilibiliProvider(account).save_draft(
            video_path=str(video), cover_path=str(cover), title=title,
            topics=topics, content_text=body)
    raise ExecutorError(f"未知平台: {platform}（支持 douyin | bilibili | channels）")


def exec_publish(ctx: StepContext) -> dict[str, str]:
    """builtin.publish：v2 发布方案 deliver→approval→publish，**三重门禁，默认关闭**。

    门禁1 配置：publish.enabled=false（默认）或 platforms 为空 → 直接跳过
    门禁2 审批：approval.json 的 decision 必须为 approve，否则拦
    门禁3 台账：send_ledger 的 claim_send_slot 抢到才发（同内容同账号不重发）
    """
    import hashlib

    from src.db import PipelineDB
    from src.utils.accounts import for_platform

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
    aspect = str(params.get("aspect") or "vertical")
    video, cover = _pick_media(ctx, aspect)
    if not video.exists():
        raise ExecutorError(f"待发布视频不存在: {video}")

    db = PipelineDB(str(ctx.db_path))
    from src.publish_ledger import (PublishBlocked, check_and_claim, record_outcome,
                                    release as ledger_release)
    video_sha = hashlib.sha256(video.read_bytes()).hexdigest()
    content_key = hashlib.sha256(
        json.dumps(draft, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    del content_key  # 去重口径已下沉到 publish_ledger（平台|账号|内容哈希），此处不再自算

    for plat in platforms:
        account = for_platform(plat)          # 无启用账号 → AccountConfigError（不静默跳过）
        acct_id = str(account.get("id") or "")

        # ---- 门禁3：平台发布台账（与飞书 send_ledger 分开，fail-closed）----
        try:
            key, _info = check_and_claim(plat, acct_id, video, str(draft.get("title") or ""))
        except PublishBlocked as exc:
            report["skipped"].append({"platform": plat, "gate": "ledger", "reason": str(exc)})
            continue

        if dry_run:
            ledger_release(key)               # 预检不占用台账位
            report["skipped"].append({"platform": plat, "gate": "dry_run",
                                      "reason": "dry_run=true，只预检不真发"})
            continue

        try:
            res = _invoke_provider(plat, account, video, cover, draft, dry_run=False)
        except Exception as exc:  # noqa: BLE001 — 结果未知，禁止自动重发
            state = record_outcome(key, False, status="SUBMIT_UNKNOWN",
                                   reason=f"{type(exc).__name__}: {exc}")
            report["failed"].append({"platform": plat, "ledger": state,
                                     "error": f"{type(exc).__name__}: {exc}"})
            continue

        if res.get("success"):
            msg_id = str(res.get("msg_id") or res.get("bvid") or res.get("draft_id")
                         or f"{plat}_{ctx.task_id}")
            state = record_outcome(key, True, external_id=msg_id)
            report["published"].append({"platform": plat, "msg_id": msg_id, "ledger": state,
                                        "status": res.get("status") or res.get("distribution") or "ok"})
        else:
            state = record_outcome(key, False, status=str(res.get("status") or ""),
                                   reason=str(res.get("message") or res.get("error") or "unknown"))
            report["failed"].append({"platform": plat, "ledger": state,
                                     "error": res.get("message") or res.get("error") or "unknown"})

    _out()
    if report["failed"]:
        raise ExecutorError("发布失败: " + ", ".join(str(f.get("platform")) for f in report["failed"]))
    return _out()


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
