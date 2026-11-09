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
import json
import os
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


def reconcile(cfg: dict[str, Any], run_root: Path) -> list[str]:
    """产物对账：run_root 不存在则视为尚未产出（dry-run 正常情况）。"""
    if not run_root.exists():
        print(f"\n=== 产物对账 ===\n  run_root 尚未创建：{run_root}（dry-run 正常）")
        return []
    print(f"\n=== 产物对账 ===\n  run_root: {run_root}")
    missing: list[str] = []
    for step in cfg["steps"]:
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


def _resolve_pending(step: dict[str, Any]) -> str:
    executor = step.get("run") or step.get("uses") or ""
    if executor in EXECUTORS_PENDING:
        return f"{executor} 待接线：{EXECUTORS_PENDING[executor]}"
    if step.get("type") == "gate":
        return f"gate {step['id']} 待接线：T4.6 审批渠道抽象"
    return f"{executor or '未知执行体'} 尚未实现"


def run_steps(cfg: dict[str, Any], run_root: Path, run_id: str,
              task_id: str, start_index: int = 0, db_path: Path = DEFAULT_DB,
              rerun_done: bool = False) -> int:
    """真实执行（T2.2：带 pipeline_runs 状态流转）。

    执行体本身仍按 `EXECUTORS_PENDING` 拒绝（T2.3/T2.5/T4.x 才接线），
    但每一步的 RUNNING / DONE / BLOCKED 都会真实落到 `pipeline_runs.steps_json`，
    这样 `--resume` 有据可依，且失败点可定位。
    """
    db = _db(db_path)
    steps = cfg["steps"]
    print("\n=== 执行 ===")
    if db:
        db.update_run_status(run_id, "RUNNING")

    for i, step in enumerate(steps):
        sid = step["id"]
        if i < start_index and not rerun_done:
            print(f"  [{sid}] SKIP（断点已过）")
            if db:
                db.set_step_status(run_id, sid, "SKIPPED")
            continue

        if db:
            db.set_step_status(run_id, sid, "RUNNING")

        pending = _resolve_pending(step)
        before = [h for h in (step.get("before_hooks") or []) if h]
        if before:
            print(f"  [{sid}] before_hooks {len(before)} 个已注册（执行接线属 T2.5）")

        if pending:
            print(f"  [{sid}] 执行体 → {pending}")
            if db:
                db.set_step_status(run_id, sid, "BLOCKED")
                db.update_run_status(run_id, "BLOCKED")
            print(f"\n拒绝执行：{pending}")
            return EXIT_NOT_IMPLEMENTED

        print(f"  [{sid}] 执行体 → OK")
        if db:
            db.set_step_status(run_id, sid, "DONE")

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
    run_id = args.run_id or f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid() % 0xFFFF:04x}"
    task_id = str((material or {}).get("task_id") or (material or {}).get("id") or run_id)
    run_root = PROJ / "output" / f"{datetime.now():%Y%m%d}" / run_id
    content_hash = content_hash_of(cfg, material)

    # 3.5 状态库（T2.2）：真实运行才落库；干跑绝不写入
    db = None if args.dry_run else _db(Path(args.db))
    if db is not None:
        existing = db.find_run_by_content(content_hash, account)
        if existing and existing.get("status") == "DONE" and not args.resume:
            print(f"\n=== 幂等跳过（uq_run_content）===\n"
                  f"  同内容已出片: run_id={existing['run_id']} status=DONE\n"
                  f"  content_hash={content_hash[:16]}… account={account}")
            return EXIT_OK
        if existing and args.resume:
            run_id = existing["run_id"]
            print(f"\n=== 续跑已有 run ===\n  run_id={run_id} status={existing.get('status')}")
        run_id = db.upsert_run(run_id, task_id, content_hash, account, run_root, status="RUNNING")

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
    if args.resume:
        start_index, why = resume_point(run_id, step_ids, Path(args.db))
        print(f"\n=== 断点续跑 ===\n  {why}")

    # 5. 计划 + 对账
    print_plan(cfg, start_index)
    missing = reconcile(cfg, run_root)
    if missing and not args.dry_run:
        print("\n拒绝执行：产物对账不通过 → " + "; ".join(missing[:3]))
        return EXIT_CONTRACT

    # 6. 执行 / 干跑收尾
    if args.dry_run:
        print("\n=== 干跑检查 ===")
        print("  · 未调用任何 LLM")
        print("  · 未写入 pipeline_runs")
        print(f"  · 待接线执行体：{len(EXECUTORS_PENDING)} 个 builtin/llm（T2.5 接线；render/dispatch 待 T4.4/T4.7）")
        if warns:
            print(f"  · 警告 {len(warns)} 条")
        print("\nDRY RUN OK")
        return EXIT_OK

    return run_steps(cfg, run_root, run_id, task_id, start_index,
                     db_path=Path(args.db), rerun_done=False)


if __name__ == "__main__":
    sys.exit(main())
