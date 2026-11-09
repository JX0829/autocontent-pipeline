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
    """llm.main：把物料扩写成 DraftScriptPayload（走 LLMClient，槽位来自 pipeline.yaml）。"""
    from src.hotspot_extractor import pick_and_convert_hotspot
    from src.schemas import DraftScriptPayload

    man = ctx.material or {}
    news_text = "\n\n".join(x for x in [str(man.get("title") or ""), str(man.get("body") or "")] if x)
    if not news_text.strip():
        raise ExecutorError("物料没有可用于扩写的正文")

    data = pick_and_convert_hotspot(news_text, task_id=ctx.task_id)
    payload = DraftScriptPayload(**data)            # schema 校验（失败即抛，不兜底）
    out = _write_json(ctx.out("draft_script.json"), payload.model_dump())
    return {"draft_script": str(out)}


def _align_and_render_vertical(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """竖版：TTS 对齐 → GeekShortVideo / CoverStill / XhsCardStill。"""
    from src.schemas import DraftScriptPayload
    from src.tts.aligner import TTSAligner

    payload = DraftScriptPayload(**draft)
    voice = os.getenv("TTS_VOICE_TECH") or "zh-CN-YunxiNeural"
    rate = os.getenv("TTS_RATE") or "+10%"
    aligner = TTSAligner(voice=voice, rate=rate)
    public_dir = RENDERER / "public"
    aligned = asyncio.run(aligner.align_draft(payload, ctx.task_id, str(public_dir)))

    props_path = public_dir / "tasks" / ctx.task_id / "aligned_render.json"
    _write_json(props_path, aligned.model_dump())
    rel_props = "./" + str(props_path.resolve().relative_to(RENDERER.resolve()))

    video = ctx.out("video.mp4")
    cover = ctx.out("cover.png")
    xhs_dir = ctx.out("xhs_cards")
    xhs_dir.mkdir(parents=True, exist_ok=True)
    xhs = xhs_dir / "card_0.png"

    _remotion("render", "src/index.ts", "GeekShortVideo", str(video),
              f"--props={rel_props}", "--codec=h264", "--crf=18", "--pixel-format=yuv420p")
    _remotion("still", "src/index.ts", "CoverStill", str(cover),
              f"--props={rel_props}", "--image-format=png")
    _remotion("still", "src/index.ts", "XhsCardStill", str(xhs),
              f"--props={rel_props}", "--image-format=png")

    return {"video": str(video), "cover": str(cover), "xhs_cards": str(xhs_dir),
            "props": str(props_path)}


def _render_landscape(ctx: StepContext, draft: dict[str, Any]) -> dict[str, str]:
    """横版：复用 scripts/render_landscape.py（内置 landscape_guard 出厂校验）。

    产物按 spec 规范名落到 run_root：`video_16_9.mp4` / `cover_16_9.png`
    （render_landscape 自己产的是 `video_landscape_16_9.mp4` / `cover_landscape.png`，
     这里复制成规范名，避免 T4.1/T4.4 的命名债扩散到新链路）。
    """
    from scripts.render_landscape import render_landscape

    props = ctx.artifacts.get("props") or str(RENDERER / "public" / "tasks" / ctx.task_id / "aligned_render.json")
    if not Path(props).exists():
        raise ExecutorError(f"横版 props 不存在: {props}（竖版 TTS 对齐未产出）")

    raw_dir = ctx.run_root / ".landscape_raw"
    info = render_landscape(ctx.task_id, props_json=props, out_dir=str(raw_dir),
                            expect_cover_text=str(draft.get("cover_main_text") or ""))

    video_src = Path(info.get("video_path_generated") or raw_dir / "video_landscape_16_9.mp4")
    cover_src = Path(info.get("cover_path") or raw_dir / "cover_landscape.png")
    video = ctx.out("video_16_9.mp4")
    cover = ctx.out("cover_16_9.png")
    video.write_bytes(video_src.read_bytes())
    cover.write_bytes(cover_src.read_bytes())
    return {"video_16_9": str(video), "cover_16_9": str(cover)}


def exec_render(ctx: StepContext) -> dict[str, str]:
    """builtin.render：按 params.aspect 分派竖版 / 横版。"""
    draft_path = ctx.artifacts.get("draft_script") or str(ctx.out("draft_script.json"))
    if not Path(draft_path).exists():
        raise ExecutorError(f"draft_script.json 不存在: {draft_path}")
    draft = json.loads(Path(draft_path).read_text(encoding="utf-8"))

    aspect = str((ctx.step.get("params") or {}).get("aspect") or "vertical")
    if aspect == "landscape":
        return _render_landscape(ctx, draft)
    return _align_and_render_vertical(ctx, draft)


def exec_approval(ctx: StepContext) -> dict[str, str]:
    """gate.approval：auto 直接放行；feishu/cli 走人工（未接入前明确拒绝，不假装通过）。"""
    mode = str((ctx.step.get("params") or {}).get("mode") or "auto")
    if mode == "auto":
        return {"approval": str(_write_json(ctx.out("approval.json"),
                                            {"mode": "auto", "decision": "approve"}))}
    raise ExecutorError(f"审批模式 {mode} 尚未接入（T4.6）：拒绝静默放行")


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
    "builtin.render": exec_render,
    "gate.approval": exec_approval,
    "builtin.dispatch": exec_dispatch,
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
