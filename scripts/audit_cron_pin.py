#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时任务模型依赖巡检（T3.4 · QA#1 cron 未 pin 导致静默断流）

背景：全局推理配置一旦漂移（换模型 / 换 provider），**未 pin 的 cron job 会被
安全机制直接跳过**，而且只在 `last_error` 里留一行字 —— 用户看到的是"任务没来"，
不是"任务失败"。实测 guanjia 的每日课程提醒已经因此停了。

判定（每个 job）：
  · `no_agent: true` + 有 `script`  → ✅ 纯脚本，无模型依赖
  · `model` 显式写死              → ✅ 已 pin
  · 其它                          → ❌ 未 pin 且依赖模型（随时可能被跳过）

用法：
    python3 scripts/audit_cron_pin.py          # 表格报告；有违例 EXIT=1
    python3 scripts/audit_cron_pin.py --json
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

JOBS_GLOB = str(Path.home() / ".hermes" / "profiles" / "*" / "cron" / "jobs.json")


def load_jobs() -> list[dict]:
    out: list[dict] = []
    for fp in sorted(glob.glob(JOBS_GLOB)):
        profile = Path(fp).parts[-3]
        try:
            data = json.loads(Path(fp).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"⚠️ 无法解析 {fp}: {exc}", file=sys.stderr)
            continue
        jobs = data.get("jobs", data) if isinstance(data, dict) else data
        for j in jobs:
            out.append({
                "profile": profile,
                "id": j.get("id"),
                "name": j.get("name") or "",
                "no_agent": bool(j.get("no_agent")),
                "script": j.get("script"),
                "model": ((j.get("model") or {}).get("model") if isinstance(j.get("model"), dict)
                          else j.get("model")),
                "provider": ((j.get("model") or {}).get("provider") if isinstance(j.get("model"), dict)
                             else j.get("provider")),
                "model_snapshot": j.get("model_snapshot"),
                "last_status": j.get("last_status"),
                "last_error": (j.get("last_error") or "")[:160],
            })
    return out


def classify(job: dict) -> tuple[str, str]:
    """返回 (状态, 说明)。状态 ∈ OK_SCRIPT / OK_PINNED / VIOLATION / WARN。"""
    if job["no_agent"]:
        if not job["script"]:
            return "WARN", "no_agent 但没有 script —— 到点不会有任何产出"
        return "OK_SCRIPT", f"纯脚本 {job['script']}"
    if job["model"]:
        return "OK_PINNED", f"{job['provider'] or '?'} / {job['model']}"
    drift = ""
    if job["model_snapshot"]:
        drift = f"（创建时 {job['model_snapshot']}，配置已漂移）"
    return "VIOLATION", f"未 pin 且依赖模型{drift}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="cron 模型依赖巡检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    jobs = load_jobs()
    rows = [(j, *classify(j)) for j in jobs]
    violations = [r for r in rows if r[1] == "VIOLATION"]
    warns = [r for r in rows if r[1] == "WARN"]

    if args.json:
        print(json.dumps({"total": len(rows),
                          "violations": [{"profile": j["profile"], "id": j["id"],
                                          "name": j["name"], "detail": d} for j, s, d in violations],
                          "warnings": [{"profile": j["profile"], "id": j["id"], "detail": d}
                                       for j, s, d in warns]},
                         ensure_ascii=False, indent=2))
        return 0 if not violations else 1

    print("=== 定时任务模型依赖巡检 ===")
    print(f"  扫描: {JOBS_GLOB}")
    print(f"  任务总数: {len(rows)}")
    for j, st, detail in rows:
        mark = {"OK_SCRIPT": "✅", "OK_PINNED": "✅", "VIOLATION": "❌", "WARN": "⚠️ "}[st]
        print(f"  {mark} [{j['profile']:12s}] {j['id']} {j['name'][:32]:32s} {st:10s} {detail}")
        if j["last_status"] and j["last_status"] != "success":
            print(f"        └ 最近状态: {j['last_status']} · {j['last_error']}")

    print()
    if violations:
        print(f"  ❌ 未 pin 且依赖模型的 job: {len(violations)} 个（全局配置一变就会被静默跳过）")
        for j, _s, _d in violations:
            print(f"     · {j['profile']}/{j['id']} {j['name']}")
        print("  修复：cronjob action=update job_id=<id> provider=<provider> model=<model>")
        return 1

    print("  ✅ 无「未 pin 且依赖模型」的 job" + (f"（另有 {len(warns)} 个 ⚠️ 警告）" if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
