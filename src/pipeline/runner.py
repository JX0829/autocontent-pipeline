#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.3 薄 Runner（spec_enhancement.md §5 T1.3）

编排层：按 `pipeline.yaml` 的顺序执行步骤，本身**不含业务逻辑**。
  · `--dry-run`：只做契约校验 + 产物对账 + 打印执行计划，**不调用任何 LLM、不写库**
  · 真实运行：每步前后调 hook，每步结束写 `pipeline_runs.status`，支持 `--resume`
  · 启动自检：按 `requires` 逐项验可执行性与版本，缺失立即拒绝运行（QA#4）

用法：
    python3 -m src.pipeline.runner --material <path|-> [--account X] [--dry-run] [--run-id ID] [--resume]

退出码：
    0   成功（dry-run 全绿）
    2   契约校验失败
    64  步骤执行体尚未实现（真实运行的前置条件未满足）
    69  运行时缺失（binaries / python 版本不达标）
"""
from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.pipeline.config import MissingConfig, load_required
from src.pipeline.contracts import (
    ContractError,
    assert_no_model_literal,
    collect_warnings,
    load_pipeline,
    validate_structure,
    verify_produces,
)
from src.pipeline.intake import ManifestInvalid, acquire, detect_carrier
from src.pipeline.manifest import load_manifest, validate

PROJ = Path(__file__).resolve().parent.parent.parent

EXIT_OK = 0
EXIT_CONTRACT = 2
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 64   # 执行体/钩子尚未实现（真实运行的前置条件未满足）
EXIT_MISSING_RUNTIME = 69   # sysexits EX_UNAVAILABLE

DEFAULT_DB = PROJ / "data" / "pipeline_state.db"


# ------------------------------------------------------------------ 启动自检

@dataclass
class RuntimeCheck:
    item: str
    expect: str
    actual: str
    ok: bool


def _cmd_version(cmd: str) -> str:
    """取命令版本串；取不到就返回 'present'。永不抛异常。"""
    for flag in ("-version", "--version", "-v"):
        try:
            p = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=8)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return "NOT FOUND"
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0 and out.strip():
            return out.strip().splitlines()[0][:60]
    return "present"


def selfcheck(requires: dict[str, Any]) -> list[RuntimeCheck]:
    """按 requires 逐项检查。binaries 缺失即 FAIL（调用方据此拒跑）。"""
    checks: list[RuntimeCheck] = []

    # python
    want = str(requires.get("python") or ">=3.10")
    cur = ".".join(str(x) for x in sys.version_info[:3])
    ok = sys.version_info >= (3, 10)
    checks.append(RuntimeCheck("python", want, f"{cur} ({sys.executable})", ok))

    # python 依赖：只查版本号会放过「解释器选错」——版本达标但包不在
    # （实测：后台 shell 解析到 miniconda 3.13，python>=3.10 通过，import openai 失败）
    dep_mods = requires.get("python_deps") or ["openai", "edge_tts", "lark_oapi"]
    missing_deps: list[str] = []
    for mod in dep_mods:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 — 任何导入失败都算依赖不可用
            missing_deps.append(f"{mod}({type(exc).__name__})")
    checks.append(RuntimeCheck(
        "python_deps", ", ".join(dep_mods),
        "缺失: " + ", ".join(missing_deps) if missing_deps else f"全部可导入 ({sys.executable})",
        not missing_deps,
    ))

    # binaries
    for name in requires.get("binaries") or []:
        path = shutil.which(name)
        checks.append(RuntimeCheck(name, "on PATH", path or "NOT FOUND", bool(path)))

    # renderer / browser / fonts（声明存在即检查，缺失按 FAIL 处理）
    if requires.get("renderer"):
        node_modules = PROJ / "renderer" / "node_modules"
        checks.append(RuntimeCheck(
            "renderer", str(requires["renderer"]),
            f"{node_modules}" if node_modules.is_dir() else "node_modules MISSING",
            node_modules.is_dir(),
        ))
    browser = requires.get("browser") or {}
    if browser:
        hits = [Path(p).expanduser() for p in browser.get("probe") or []]
        found = next((str(h) for h in hits if h.exists()), "")
        if not found:
            sys_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            found = str(sys_chrome) if sys_chrome.exists() else ""
        checks.append(RuntimeCheck(
            "browser", "chrome-headless-shell / system Chrome",
            found or "MISSING（跑 scripts/bootstrap.py --ensure）", bool(found),
        ))
    if requires.get("fonts"):
        checks.append(RuntimeCheck(
            "fonts", ", ".join(requires["fonts"]),
            "未强制校验（OCR 判据在 T4.4 落地时校验）", True,
        ))
    return checks


def print_selfcheck(checks: list[RuntimeCheck]) -> None:
    print("=== 启动自检（requires）===")
    w = max(len(c.item) for c in checks)
    for c in checks:
        print(f"  {c.item:<{w}}  {c.actual:<52}  {'OK' if c.ok else 'FAIL'}   (期望 {c.expect})")


# ------------------------------------------------------------------ 执行计划

def print_plan(cfg: dict[str, Any], start_index: int = 0) -> None:
    print("\n=== 执行计划 ===")
    rows = []
    for i, step in enumerate(cfg["steps"], 1):
        executor = step.get("run") or step.get("uses") or f"gate:{step.get('type')}"
        reads = ", ".join(step.get("reads") or []) or "—"
        produces = ", ".join(step.get("produces") or []) or "—"
        hooks = str(len(step.get("before_hooks") or []) + len(step.get("after_hooks") or []))
        rows.append((str(i), step["id"], str(executor), reads, produces, hooks))

    widths = [2, 4, 6, 5, 8, 5]
    for r in rows:
        for j in range(1, 6):
            widths[j] = max(widths[j], len(r[j]))
    widths[1] = max(widths[1], len("step"))

    print("  " + "  ".join(
        ["#".rjust(widths[0]), "step".ljust(widths[1]), "执行体".ljust(widths[2]),
         "reads".ljust(widths[3]), "produces".ljust(widths[4]), "hooks".ljust(widths[5])]
    ))
    print("  " + "-" * (sum(widths) + 2 * 5))
    for idx, r in enumerate(rows, 1):
        mark = "  " if idx > start_index else "✓ "
        print("  " + mark + "  ".join([
            r[0].rjust(widths[0]), r[1].ljust(widths[1]), r[2].ljust(widths[2]),
            r[3].ljust(widths[3]), r[4].ljust(widths[4]), r[5].ljust(widths[5]),
        ]).rstrip())
    if start_index:
        print(f"  （按 --resume 从第 {start_index + 1} 步继续）")


def reconcile(cfg: dict[str, Any], run_root: Path, upto: int | None = None) -> list[str]:
    """产物对账：run_root 不存在则视为尚未产出（dry-run 正常情况）。

    `upto` = 只对账「理应已完成」的步骤（断点续跑时，后面的步骤本来就没产物）。
    """
    if not run_root.exists():
        print(f"\n=== 产物对账 ===\n  run_root 尚未创建：{run_root}（dry-run 正常）")
        return []
    print(f"\n=== 产物对账 ===\n  run_root: {run_root}")
    missing: list[str] = []
    for i, step in enumerate(cfg["steps"]):
        if upto is not None and i >= upto:
            break
        miss = verify_produces(step, run_root)
        status = "OK" if not miss else f"MISSING {len(miss)}"
        print(f"  {step['id']:<18} {status}")
        missing.extend(miss)
    return missing


# ------------------------------------------------------------------ 状态库

def _db(db_path: Path = DEFAULT_DB):
    """惰性构造 PipelineDB（T2.2）。库/表缺失 → None，调用方按"无状态库"降级。"""
    try:
        from src.db import PipelineDB
        return PipelineDB(str(db_path))
    except Exception:
        return None


def content_hash_of(cfg: dict[str, Any], material: dict[str, Any] | None) -> str:
    """内容指纹：归一化 title + body。用于 (content_hash, account) 唯一约束与去重。"""
    from src.pipeline.manifest import load_manifest
    from src.pipeline.intake import ManifestInvalid
    if not material:
        return hashlib.sha256(b"no-material").hexdigest()
    try:
        m = load_manifest(material)
    except (ValueError, ManifestInvalid):
        return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    norm = re.sub(r"\s+", "", (m.title or "") + "|" + (m.body or ""))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def record_status(run_id: str, task_id: str, step_id: str, status: str,
                  run_root: str, db_path: Path = DEFAULT_DB) -> bool:
    """写步骤级状态到 pipeline_runs.steps_json（T2.2）。失败返回 False，不抛。"""
    db = _db(db_path)
    if db is None:
        return False
    try:
        db.upsert_run(run_id, task_id, "", "", run_root)
        return db.set_step_status(run_id, step_id, status)
    except Exception:
        return False


def resume_point(run_id: str, step_ids: list[str], db_path: Path = DEFAULT_DB) -> tuple[int, str]:
    """返回 (从第几步开始(0-based), 说明)。读 pipeline_runs.steps_json（T2.2）。"""
    db = _db(db_path)
    if db is None:
        return 0, "状态库不可用，从头开始"
    try:
        run = db.get_run(run_id)
    except Exception:
        return 0, "状态库读取失败，从头开始"
    if not run:
        return 0, "无该 run 记录，从头开始"
    steps = run.get("steps") or {}
    done = {sid for sid, st in steps.items() if st in ("DONE", "SKIPPED")}
    for i, sid in enumerate(step_ids):
        if sid not in done:
            return i, f"已完成 {len(done)}/{len(step_ids)} 步，从 {sid} 继续"
    return len(step_ids), "全部步骤已完成"


# ------------------------------------------------------------------ 执行体

HOOKS_PENDING = "hook 执行引擎未实现（T2.1）"
# 执行体接线状态（组件已具备，缺的是「把组件接到 step 上」这一步 = T2.5）
EXECUTORS_PENDING = {
    "builtin.intake": "T2.5 执行接线（intake.py 已就绪）",
    "builtin.check_material": "T2.5 执行接线（manifest 校验已就绪）",
    "builtin.render": "T4.4 渲染执行体",
    "builtin.dispatch": "T4.7 发布执行体",
    "llm.main": "T2.5 执行接线（LLMClient 已就绪）",
    "llm.fast": "T2.5 执行接线（LLMClient 已就绪）",
}


def find_runs_for_identity(db, content_hash: str, account: str,
                           explicit_run_id: str) -> tuple[dict[str, Any] | None,
                                                          dict[str, Any] | None]:
    """返回 (同内容历史 run, 显式 run-id 指向的那条 run)。

    两个查询解决两件不同的事：
      · 同内容（uq_run_content）：判断「这条内容是不是已经出完片了」→ 防重复出片/重发；
      · 显式 run_id：用户点名的 run，必须按 **id** 取，不能按内容猜 —— 按内容猜就是
        「换新 run_id 却仍去读上一版已删目录」那个 bug 的根。
    """
    content_run = db.find_run_by_content(content_hash, account)
    named_run = db.get_run(explicit_run_id) if explicit_run_id else None
    return content_run, named_run


def resolve_run_identity(content_run: dict[str, Any] | None,
                         named_run: dict[str, Any] | None, *, explicit_run_id: str,
                         resume: bool, new_run_id: str, new_run_root: Path,
                         task_id: str) -> tuple[str, Path, str, str, bool]:
    """决定本次 run 的 (run_id, run_root, task_id, mode, force_new)。

    这是「重跑必崩」的第三个成因（2026-11-11 17:19）：用户显式传新 `--run-id`
    重跑时，原实现被 `find_run_by_content` 查到的旧 run **无条件覆盖**，于是仍去读
    上一版（BLOCKED）的产物目录 —— 那个目录里往往是已删/半成品，报的是
    「draft_script 不存在」，看不出是 run 身份被劫持。

    但**不能**把「显式优先」无限放大：`uq_run_content` 上的幂等跳过是防重复出片/重发的
    承重设计（GATE-3 的 B 段就是这个契约），同内容已 DONE 时必须继续跳过。

    优先级（从上到下命中即返回）：
      skip          同内容已 DONE 且非 resume → 幂等跳过（即便显式给了新 run_id）
      retry/resume  显式 run_id 指向的 run 存在 → 用它自己的 run_root（不按内容猜）
      explicit_new  显式 run_id 在库里没有 → 全新目录；若同内容有未完成的 run，
                    调用方要用 force_new 归档它的 content_hash（唯一索引让路）
      retry         同内容历史 run 未完成（未显式 run_id）→ 复用原 run_id/run_root
      resume        显式 --resume → 回到原 run
      fresh         没有任何历史
    """
    if content_run and content_run.get("status") == "DONE" and not resume:
        return new_run_id, new_run_root, task_id, "skip", False

    if named_run:
        run_id = named_run["run_id"] or new_run_id
        run_root = Path(named_run["run_root"]) if named_run.get("run_root") else new_run_root
        task_id = str(named_run.get("task_id") or task_id)
        return run_id, run_root, task_id, ("resume" if resume else "retry"), False

    if explicit_run_id:
        return explicit_run_id, new_run_root, task_id, "explicit_new", bool(content_run)

    if content_run:
        run_id = content_run["run_id"] or new_run_id
        # 断点续跑/重试必须回到**原来那个 run_root**，否则找不到上一步产物
        run_root = Path(content_run["run_root"]) if content_run.get("run_root") else new_run_root
        # task_id 也必须回到原值：TTS props 落在 renderer/public/tasks/<task_id>/
        task_id = str(content_run.get("task_id") or task_id)
        return run_id, run_root, task_id, ("resume" if resume else "retry"), False

    return new_run_id, new_run_root, task_id, "fresh", False


def _resolve_pending(step: dict[str, Any]) -> str:
    executor = step.get("run") or step.get("uses") or ""
    if executor in EXECUTORS_PENDING:
        return f"{executor} 待接线：{EXECUTORS_PENDING[executor]}"
    if step.get("type") == "gate":
        return f"gate {step['id']} 待接线：T4.6 审批渠道抽象"
    return f"{executor or '未知执行体'} 尚未实现"


def dispatch_policy(cfg: dict[str, Any]) -> tuple[bool, str, list[str]]:
    """T4.7 出片/发片解耦：返回 (output_only, mode, aspects)。"""
    steps = cfg.get("steps") or []
    dparams = next((s.get("params") or {} for s in steps
                    if s.get("id") in ("deliver", "dispatch")), {})
    mode = str(dparams.get("mode") or "publish")
    enabled = bool(dparams.get("enabled", True))
    output_only = (not enabled) or mode == "output_only"

    raw = (cfg.get("inputs") or {}).get("aspects") or []
    if isinstance(raw, dict):
        raw = raw.get("default") or []
    aspects = [str(a) for a in raw]
    if not aspects:
        raise ContractError("inputs.aspects 为空：必须至少声明 vertical 或 landscape")
    bad = [a for a in aspects if a not in ("vertical", "landscape")]
    if bad:
        raise ContractError(f"inputs.aspects 含非法取值: {bad}")
    return output_only, mode, aspects


def write_artifacts_manifest(run_root: Path, artifacts: dict[str, str], mode: str) -> Path:
    """output_only 时把产物绝对路径落盘并打印。

    按 aspect 分组输出（竖版/横版），避免交付方手工挑选时把
    「竖版封面 + 横版视频」这种错配组合发出去。
    """
    ASPECT_LABEL = {
        "video": ("vertical", "竖版视频"),
        "cover": ("vertical", "竖版封面"),
        "video_16_9": ("landscape", "横版视频"),
        "cover_16_9": ("landscape", "横版封面"),
    }
    groups: dict[str, list[str]] = {"vertical": [], "landscape": [], "other": []}
    for key in sorted(artifacts):
        path = Path(artifacts[key])
        targets = ([q for q in sorted(path.glob("*")) if q.is_file()]
                   if path.is_dir() else ([path] if path.exists() else []))
        for q in targets:
            group, label = ASPECT_LABEL.get(key, ("other", key))
            groups[group].append(f"{label if key in ASPECT_LABEL else key}={q.resolve()}")

    lines: list[str] = []
    for group, title in (("vertical", "竖版 1080x1920"), ("landscape", "横版 1920x1080")):
        if groups[group]:
            lines.append(f"[{group}] {title}")
            lines.extend(groups[group])
    if groups["other"]:
        lines.append("[other] 非交付物（脚本/中间态）")
        lines.extend(groups["other"])

    out = run_root / "artifacts.txt"
    out.parent.mkdir(parents=True, exist_ok=True)   # run_root 可能尚未创建（独立调用/测试场景）
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"\n=== 只出片（mode={mode}）产物清单 ===")
    for line in lines:
        print(f"  {line}")
    media = len(groups["vertical"]) + len(groups["landscape"])
    if media:
        print(f"  ⚠️ 交付时竖版与横版各 {media // 2} 个文件都要发，别只挑其中一半")
    print(f"  清单文件: {out}")
    return out


def _hook_block_severity(cfg: dict[str, Any], name: str) -> str:
    """hook 的阻断语义：`on_fail: block` 才拦流程，`warn`/observe 只记录。"""
    spec = ((cfg.get("hooks") or {}).get(name) or {})
    return str(spec.get("on_fail") or ("block" if spec.get("type") == "gate" else "warn"))


def _run_hooks(cfg: dict[str, Any], names: list[str], payload: dict[str, Any],
               strict: bool = True) -> tuple[bool, list[dict[str, Any]]]:
    """执行一组 hook，返回 (是否放行, 结果列表)。阻断级失败 → 不放行。

    `type: transform` 的 hook 会把 `patch` 合并回 `context`，这样下游（执行体或
    下一个 hook）真能看到它的产出 —— 否则 transform 等于白跑（T3.x 实测：
    expand_narration 切好的 narration 不落 context，audio_duration_check 永远告警）。
    """
    from .hooks import run_hook

    results: list[dict[str, Any]] = []
    blocked = False
    ctx = payload.setdefault("context", {})
    for name in names:
        if not name:
            continue
        res = run_hook(name, payload, cfg=cfg)
        results.append(res.as_dict())
        if res.ok and isinstance(res.patch, dict) and res.patch:
            for k, v in res.patch.items():
                if k in ("card", "schema"):
                    continue
                ctx[k] = v
        if not res.ok:
            sev = _hook_block_severity(cfg, name)
            print(f"      ! {name}: {res.reason[:120]}（on_fail={sev}）")
            if strict and sev == "block":
                blocked = True
    return (not blocked), results


def _discover_artifacts(run_root: Path) -> dict[str, str]:
    """断点续跑时从 run_root 恢复已产出物（key = 文件名去扩展名）。

    没有这一步，`--resume` 跳过渲染步后 artifacts 仍是空的，
    下游 hook（如 send_ledger）会报"找不到待发视频"而把整步阻断 —— C 步实测踩到。
    """
    found: dict[str, str] = {}
    if not run_root.is_dir():
        return found
    for p in sorted(run_root.iterdir()):
        if p.is_file() and p.suffix.lower() in {".mp4", ".png", ".json", ".mp3"}:
            found[p.stem] = str(p)
    return found


def notify_render_done(cfg: dict[str, Any], run_id: str, run_root: Path,
                       artifacts: dict[str, str]) -> None:
    """出片一完成立即汇报（不等审批/投递）。失败只告警，绝不影响出片流程。"""
    ncfg = cfg.get("notify") or {}
    if not ncfg.get("render_done", True):
        return
    vids = [(k, artifacts[k]) for k in ("video", "video_16_9") if artifacts.get(k)]
    if not vids:
        return

    lines = [f"✅ 出片完成 · {run_id}"]
    for key, path in vids:
        f = Path(path)
        size = f"{f.stat().st_size / 1e6:.1f}MB" if f.exists() else "缺失"
        lines.append(f"  {key}: {f.name}（{size}）")
    lines.append(f"  目录: {run_root}")

    try:
        from src.feishu.approval_service import FeishuApprovalService
        chat = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
        if not chat:
            print("  ! 出片通知跳过：未配置 FEISHU_APPROVAL_CHAT_ID")
            return
        # send_text_message 需要 (receive_id, receive_id_type, text) —— 真实签名在真机上核对过
        id_type = "chat_id" if chat.startswith("oc_") else "open_id"
        msg_id = FeishuApprovalService().send_text_message(chat, id_type, "\n".join(lines))
        print(f"  ✓ 出片完成已汇报（飞书 msg_id={msg_id}）")
    except Exception as exc:  # noqa: BLE001 — 通知失败不能把出片判失败
        print(f"  ! 出片通知失败（不影响出片）：{type(exc).__name__}: {exc}")


def _execute_step(cfg: dict[str, Any], step: dict[str, Any], *,
                  artifacts: dict[str, str], run_id: str, task_id: str,
                  run_root: Path, material: dict[str, Any] | None,
                  account: str, db_path: Path) -> tuple[bool, dict[str, str], str]:
    """执行单个 step（before_hooks → 执行体 → after_hooks）。

    **不写数据库**：状态回写交给主线程，这样并行组里的多个 step 不会争同一连接。
    返回 (是否成功, 产物, 失败原因)。
    """
    from .executors import ExecutorError, StepContext, run_step

    sid = step["id"]
    params = step.get("params") or {}
    aspect = str(params.get("aspect") or "")
    kind = str(step.get("artifact_kind") or {"vertical": "vertical",
                                             "landscape": "landscape"}.get(aspect, ""))
    draft: dict[str, Any] = {}
    if artifacts.get("draft_script"):
        try:
            draft = json.loads(Path(artifacts["draft_script"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"      ! draft_script 读取失败: {exc}")

    payload = {
        "task_id": task_id, "run_id": run_id, "run_root": str(run_root),
        "step_id": sid, "artifacts": dict(artifacts),
        "context": {
            "run_id": run_id, "account": account or (material or {}).get("account"),
            "manifest": material, "db_path": str(db_path),
            "artifact_kind": kind,
            "aspect": {"vertical": "9:16", "landscape": "16:9"}.get(aspect, aspect),
            "landscape_cover": str(params.get("landscape_cover") or ""),
            "video_key": "video_16_9" if kind == "landscape" else "video",
            "cover_key": "cover_16_9" if kind == "landscape" else "cover",
            "draft": draft,
            "expect_text": str(draft.get("cover_main_text") or ""),
        },
    }

    before = [h for h in (step.get("before_hooks") or []) if h]
    if before:
        ok, _ = _run_hooks(cfg, before, payload)
        if not ok:
            return False, {}, "before_hooks 阻断"

    # 自动重试：pipeline.yaml 每步声明的 retry:{max,on_schema_error} 真实生效
    # （此前是死配置 —— 声明了没人读，LLM 超时一次就整步失败）
    rcfg = step.get("retry") or {}
    try:
        max_attempts = max(1, int(rcfg.get("max") or 1))
    except (TypeError, ValueError):
        max_attempts = 1
    backoff = 2.0
    produced: dict[str, str] = {}
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        try:
            produced = run_step(step, StepContext(
                step=step, run_root=run_root, run_id=run_id, task_id=task_id,
                material=material, artifacts=dict(artifacts),
                params=params, db_path=str(db_path),
            ))
            last_err = ""
            break
        except ExecutorError as exc:
            last_err = str(exc)
        except Exception as exc:  # noqa: BLE001 — 超时/网络等非 ExecutorError 也必须落库成 BLOCKED
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < max_attempts:
            print(f"      · 第 {attempt}/{max_attempts} 次失败：{last_err[:140]} → 重试")
            time.sleep(backoff * attempt)
    if last_err:
        return False, {}, (f"执行失败（{max_attempts}/{max_attempts} 次尝试）：{last_err}"
                           if max_attempts > 1 else f"执行失败：{last_err}")

    merged = dict(artifacts)
    merged.update(produced or {})

    after = [h for h in (step.get("after_hooks") or []) if h]
    if after:
        payload["artifacts"] = dict(merged)
        payload["context"]["artifacts"] = dict(merged)
        ok, _ = _run_hooks(cfg, after, payload)
        if not ok:
            return False, {}, "after_hooks 阻断"
    return True, produced or {}, ""


def run_steps(cfg: dict[str, Any], run_root: Path, run_id: str,
              task_id: str, start_index: int = 0, db_path: Path = DEFAULT_DB,
              rerun_done: bool = False, material: dict[str, Any] | None = None,
              account: str = "") -> int:
    """真实执行（T2.2 状态流转 + P5 执行体接线 + hook 执行）。

    每一步：before_hooks → 执行体 → after_hooks，产物写进 run_root。
    `on_fail: block` 的 hook 失败或执行体异常 → 该步 BLOCKED、run BLOCKED、退出码 64。
    """
    from .executors import ExecutorError, StepContext, run_step

    db = _db(db_path)
    steps = cfg["steps"]
    artifacts: dict[str, str] = _discover_artifacts(run_root) if start_index > 0 else {}

    # T4.7 出片/发片解耦：output_only 不审批不发送；aspects 决定要哪几版成片
    output_only, dispatch_mode, aspects = dispatch_policy(cfg)
    skip: dict[str, str] = {}
    if output_only:
        skip["approval"] = "output_only 不审批"
        skip["deliver"] = "output_only 不投递"
    skip["dispatch"] = "output_only 不投递"
    if "vertical" not in aspects:
        skip["render_vertical"] = "aspects 未包含 vertical"
    if "landscape" not in aspects:
        skip["render_landscape"] = "aspects 未包含 landscape"

    # v2 发布方案门禁1：publish.enabled 默认 false → 整步 SKIP（不进入执行体）
    _pub_step = next((s for s in steps if s.get("id") == "publish"), {})
    pparams = {**((cfg.get("publish") or {})), **(_pub_step.get("params") or {})}
    if not bool(pparams.get("enabled", False)) or not (pparams.get("platforms") or []):
        skip["publish"] = "publish.enabled=false 或 platforms 为空（默认关闭）"

    print("\n=== 执行 ===")
    print(f"  出片策略: mode={dispatch_mode} · output_only={output_only} · aspects={aspects}")
    if db:
        db.update_run_status(run_id, "RUNNING")

    i = 0
    while i < len(steps):
        step = steps[i]
        sid = step["id"]

        if i < start_index and not rerun_done:
            print(f"  [{sid}] SKIP（断点已过）")
            if db:
                db.set_step_status(run_id, sid, "SKIPPED")
            i += 1
            continue

        if sid in skip:
            print(f"  [{sid}] SKIP（{skip[sid]}）")
            if db:
                db.set_step_status(run_id, sid, "SKIPPED")
            i += 1
            continue

        # 收集同组连续步（并行组）
        group = step.get("parallel_group")
        batch = [step]
        nxt = i + 1
        if group:
            while (nxt < len(steps)
                   and steps[nxt].get("parallel_group") == group
                   and steps[nxt]["id"] not in skip
                   and nxt >= start_index):
                batch.append(steps[nxt])
                nxt += 1

        if len(batch) == 1:
            if db:
                db.set_step_status(run_id, sid, "RUNNING")
            print(f"  [{sid}] …")
            ok, produced, why = _execute_step(
                cfg, step, artifacts=artifacts, run_id=run_id, task_id=task_id,
                run_root=run_root, material=material, account=account, db_path=db_path)
            if not ok:
                print(f"  [{sid}] {why}")
                if db:
                    db.set_step_status(run_id, sid, "BLOCKED")
                    db.update_run_status(run_id, "BLOCKED")
                return EXIT_NOT_IMPLEMENTED
            artifacts.update(produced)
            print(f"  [{sid}] 产出: {', '.join(sorted(produced)) or '（无）'}")
            if db:
                db.set_step_status(run_id, sid, "DONE")
            if sid.startswith("render"):
                notify_render_done(cfg, run_id, run_root, artifacts)
        else:
            print(f"  [{group}] 并行执行 {len(batch)} 步: {', '.join(s['id'] for s in batch)}")
            for s in batch:
                if db:
                    db.set_step_status(run_id, s["id"], "RUNNING")
                print(f"  [{s['id']}] …")
            snapshot = dict(artifacts)   # 组内共享同一份输入快照
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                futures = [
                    pool.submit(_execute_step, cfg, s, artifacts=snapshot, run_id=run_id,
                                task_id=task_id, run_root=run_root, material=material,
                                account=account, db_path=db_path)
                    for s in batch
                ]
                results = [(s, f.result()) for s, f in zip(batch, futures)]

            blocked = False
            for s, (ok, produced, why) in results:
                if ok:
                    artifacts.update(produced)
                    print(f"  [{s['id']}] 产出: {', '.join(sorted(produced)) or '（无）'}")
                    if db:
                        db.set_step_status(run_id, s["id"], "DONE")
                else:
                    print(f"  [{s['id']}] {why}")
                    if db:
                        db.set_step_status(run_id, s["id"], "BLOCKED")
                    blocked = True
            if blocked:
                if db:
                    db.update_run_status(run_id, "BLOCKED")
                return EXIT_NOT_IMPLEMENTED
            if any(str(s["id"]).startswith("render") for s in batch):
                notify_render_done(cfg, run_id, run_root, artifacts)

        i = nxt

    if output_only:
        write_artifacts_manifest(run_root, artifacts, dispatch_mode)

    if db:
        db.update_run_status(run_id, "DONE")
    print("\n执行完成")
    return EXIT_OK


# ------------------------------------------------------------------ 入口

def _read_material(path: str, inbox: str | None = None) -> dict[str, Any] | None:
    """统一投料入口（T1.8）：cli 路径 / stdin / feishu 三载体共用一份 schema。

    `/dev/null` 仅用于「无物料干跑」。校验不通过直接 `ContractError` → EXIT=2，
    不做任何载体级字段补全。
    """
    if path in ("NUL",) or path == "/dev/null":
        return None
    try:
        got = acquire(path, inbox=inbox)
    except ManifestInvalid as exc:
        raise ContractError(str(exc)) from exc
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"取物料失败（carrier={detect_carrier(path)}）: {exc}") from exc
    return got.raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AutoContent Pipeline 薄 Runner（T1.3）")
    ap.add_argument("--pipeline", default="pipeline.yaml", help="声明文件路径")
    ap.add_argument("--material", required=True,
                    help="物料：路径 / -（stdin） / feishu（轮询投料箱） / /dev/null（干跑无物料）")
    ap.add_argument("--inbox", default=None, help="feishu 投料箱目录（默认 <项目>/inbox）")
    ap.add_argument("--account", default=None, help="目标账号，覆盖 inputs.account.default")
    ap.add_argument("--dry-run", action="store_true", help="只校验与打印计划，不调用任何模型、不写库")
    ap.add_argument("--run-id", default=None, help="指定 run_id（默认按时间生成）")
    ap.add_argument("--resume", action="store_true", help="按 pipeline_runs 从断点继续")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="状态库路径")
    args = ap.parse_args(argv)

    # 加载声明文件
    try:
        cfg = load_pipeline(args.pipeline)
    except ContractError as exc:
        print(f"[contract] {exc}", file=sys.stderr)
        return EXIT_CONTRACT

    # 0. 配置 fail-fast（T1.9 / G-B）：必填 env 缺失立即退出 78，绝不跑到第 3 步才炸
    print("=== 配置校验 ===")
    try:
        load_required(cfg)
        print("  必填键: 齐全")
    except MissingConfig as exc:
        print(f"  missing: {', '.join(exc.missing)}", file=sys.stderr)
        print(f"  参考 .env.example 补齐后重试（EXIT={exc.code}）", file=sys.stderr)
        return exc.code

    print("=== 契约校验 ===")
    errors = validate_structure(cfg)
    warns = collect_warnings(cfg)
    print(f"  结构错误: {len(errors)}    警告: {len(warns)}")
    for e in errors:
        print(f"  ✗ {e}")
    if errors:
        return EXIT_CONTRACT
    try:
        assert_no_model_literal(cfg)
        print("  模型名泄漏: 无")
    except ContractError as exc:
        print(f"  ✗ {exc}")
        return EXIT_CONTRACT

    # 2. 启动自检（QA#4）
    checks = selfcheck(cfg.get("requires") or {})
    print_selfcheck(checks)
    fatal = [c for c in checks if not c.ok]
    if fatal:
        print(f"\n拒绝运行：{len(fatal)} 项运行时缺失 → "
              + ", ".join(c.item for c in fatal)
              + "\n  先跑：python3 scripts/bootstrap.py --ensure")
        return EXIT_MISSING_RUNTIME

    # 3. 物料与运行上下文
    try:
        material = _read_material(args.material, args.inbox)
    except ContractError as exc:
        print(f"[material] {exc}", file=sys.stderr)
        return EXIT_CONTRACT
    account = args.account or ((cfg.get("inputs") or {}).get("account") or {}).get("default") \
        or ((material or {}).get("account"))
    account = account or "unknown"
    explicit_run_id = str(args.run_id or "")
    run_id = explicit_run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 0xFFFF:04x}"
    task_id = str((material or {}).get("task_id") or (material or {}).get("id") or run_id)
    run_root = PROJ / "output" / f"{datetime.now():%Y%m%d}" / run_id
    content_hash = content_hash_of(cfg, material)

    # 3.5 状态库（T2.2）：真实运行才落库；干跑绝不写入
    db = None if args.dry_run else _db(Path(args.db))
    auto_resume = False
    if db is not None:
        # 两个查询各管一件事：同内容（幂等跳过）+ 显式 run_id（按 id 取，不按内容猜）
        content_run, named_run = find_runs_for_identity(db, content_hash, account, explicit_run_id)
        run_id, run_root, task_id, mode, force_new = resolve_run_identity(
            content_run, named_run, explicit_run_id=explicit_run_id,
            resume=bool(args.resume), new_run_id=run_id, new_run_root=run_root,
            task_id=task_id)
        if mode == "skip":
            assert content_run is not None   # skip 只可能在查到同内容 DONE 时返回
            print(f"\n=== 幂等跳过（uq_run_content）===\n"
                  f"  同内容已出片: run_id={content_run['run_id']} status=DONE\n"
                  f"  content_hash={content_hash[:16]}… account={account}")
            return EXIT_OK
        if mode == "explicit_new":
            if content_run:
                print(f"\n=== 显式 --run-id 优先 ===\n"
                      f"  run_id={run_id}（同内容存在未完成 run {content_run['run_id']}，"
                      f"不复用其目录；旧行保留备查）")
        if mode == "retry":
            # 同内容历史 run 处于 FAILED/RUNNING/PARTIAL 时按「重试」处理，
            # 复用原 run_id 与原 run_root —— 否则新 run_root 与库里 run_id 错配，
            # 续跑/对账必然找不到产物，表现为「重跑必崩、永远卡住」。
            auto_resume = True
            print(f"\n=== 同内容历史 run 重试 ===\n"
                  f"  run_id={run_id} status={(named_run or content_run or {}).get('status')}\n"
                  f"  复用 run_root={run_root}\n"
                  f"  （如需全新一版，请显式传 --run-id <新 id>）")
        if mode == "resume":
            print(f"\n=== 续跑已有 run ===\n  run_id={run_id} "
                  f"status={(named_run or content_run or {}).get('status')}"
                  f"\n  run_root={run_root}")
        run_id = db.upsert_run(run_id, task_id, content_hash, account, run_root,
                               status="RUNNING", force_new=force_new)

        # 收尸：进程被外部杀掉时状态库会永远停在 RUNNING（2026-11-10 的 astryx-1/freecc-1）。
        # 这里只如实标记，不删产物；同内容重跑仍按「重试」复用原 run_id 与原目录。
        try:
            reaped = db.fail_stale_runs(int(os.getenv("STALE_RUN_MINUTES", "15")))
            if reaped:
                print(f"\n=== 清理僵尸 RUNNING ===\n  {', '.join(reaped)} → ABANDONED（超过阈值未更新）")
        except Exception as exc:  # noqa: BLE001 — 收尸失败不能阻断本次运行
            print(f"  [warn] 僵尸 RUNNING 清理失败: {type(exc).__name__}: {exc}")

    print("\n=== 运行上下文 ===")
    print(f"  run_id   : {run_id}")
    print(f"  task_id  : {task_id}")
    print(f"  account  : {account}")
    print(f"  content  : {content_hash[:16]}…")
    print(f"  material : {'(无物料)' if material is None else f'{len(material)} 个字段'}")
    print(f"  run_root : {run_root}")
    print(f"  状态库   : {args.db if db is not None else '(dry-run 未写库)'}")
    print(f"  模式     : {'DRY-RUN' if args.dry_run else 'REAL'}")

    # 4. 断点
    step_ids = [s["id"] for s in cfg["steps"]]
    start_index = 0
    if args.resume or auto_resume:
        start_index, why = resume_point(run_id, step_ids, Path(args.db))
        label = "断点续跑" if args.resume else "同内容重试续跑"
        print(f"\n=== {label} ===\n  {why}")

    # 5. 计划 + 对账
    print_plan(cfg, start_index)
    missing = reconcile(cfg, run_root, upto=start_index if args.resume else None)
    if missing and not args.dry_run:
        print("\n拒绝执行：产物对账不通过 → " + "; ".join(missing[:3]))
        return EXIT_CONTRACT

    # 6. 执行 / 干跑收尾
    if args.dry_run:
        print("\n=== 干跑检查 ===")
        print("  · 未调用任何 LLM")
        print("  · 未写入 pipeline_runs")
        from .executors import EXECUTORS
        wired = [s["id"] for s in cfg["steps"] if (s.get("run") or s.get("uses") or s.get("type"))]
        unwired = [s["id"] for s in cfg["steps"]
                   if (s.get("run") or s.get("uses")) and (s.get("run") or s.get("uses")) not in EXECUTORS]
        print(f"  · 执行体接线：{len(wired) - len(unwired)}/{len(wired)} 已接"
              + (f"；未接 {', '.join(unwired)}" if unwired else ""))
        if warns:
            print(f"  · 警告 {len(warns)} 条")
        print("\nDRY RUN OK")
        return EXIT_OK

    return run_steps(cfg, run_root, run_id, task_id, start_index,
                     db_path=Path(args.db), rerun_done=False, material=material,
                     account=account)


if __name__ == "__main__":
    sys.exit(main())
