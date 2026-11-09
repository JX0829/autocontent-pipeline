#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GATE-3 · 端到端不重发验收（runner 全链，假传输替代真实飞书）

为什么用假传输：验收脚本**不允许**往真实飞书群发视频。除了 `upload_and_send_video`
被替换成一个只计数、返回假 msg_id 的实现之外，其余全部走生产代码路径
（intake → check_material → llm 扩写 → TTS → Remotion 渲染 → 审批 → 台账 → 分发）。

三条断言：
  A. 首次全链跑通 → 发送调用 1 次、send_ledger 1 行
  B. 同一物料再跑一次 → 命中 uq_run_content「幂等跳过」，发送仍 1 次、台账仍 1 行
  C. 抹掉 pipeline_runs 记录（模拟本地状态丢失）后 --resume 重发
     → 命中 send_ledger「already_sent」，发送仍 1 次、台账仍 1 行

用法：
    python3 scripts/_gate3_e2e.py [--keep]      # --keep 保留临时库与产物目录
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

# ---------------------------------------------------------------- 假传输
import src.feishu.approval_service as _svc  # noqa: E402

SENT: list[dict] = []


class _FakeApprovalService:
    def __init__(self, *a, **k):
        pass

    def upload_and_send_video(self, **kw):
        SENT.append(kw)
        return f"om_fake_{len(SENT):03d}"


_svc.FeishuApprovalService = _FakeApprovalService

from src.db import PipelineDB  # noqa: E402
from src.pipeline import runner  # noqa: E402

MATERIAL = Path("/tmp/gate3_material.json")
DB = Path("/tmp/gate3_state.db")

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        failures.append(f"{name} {detail}".strip())


def run(rid: str, extra: list[str] | None = None) -> tuple[int, str]:
    argv = ["runner", "--material", str(MATERIAL), "--db", str(DB), "--run-id", rid]
    argv += extra or []
    old = sys.argv[:]
    sys.argv = argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = runner.main()
    finally:
        sys.argv = old
    return rc, buf.getvalue()


def rows(table: str) -> int:
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时库与产物目录")
    args = ap.parse_args()

    today = f"{datetime.now():%Y%m%d}"
    MATERIAL.write_text(json.dumps({
        "title": "DeepSeek V4 发布：推理提速三倍",
        "body": "官方正式发布 DeepSeek V4，推理速度提升三倍，成本下降一半，开源权重同步放出。",
        "account": "douyin-tech-main",
    }, ensure_ascii=False), encoding="utf-8")

    if DB.exists():
        DB.unlink()
    for rid in ("g3-a", "g3-b"):
        shutil.rmtree(PROJ / "output" / today / rid, ignore_errors=True)

    print("=" * 72)
    print("GATE-3 · 端到端不重发验收（真实全链 + 假飞书传输）")
    print("=" * 72)

    # ---------------------------------------------------------------- A
    print("\n[A] 首次全链执行")
    rc_a, out_a = run("g3-a")
    print(out_a.strip().splitlines()[-1] if out_a.strip() else "(无输出)")
    check("A1 runner 退出码 0", rc_a == 0, f"rc={rc_a}")
    check("A2 飞书发送调用 2 次（竖+横）", len(SENT) == 2, f"calls={len(SENT)}")
    check("A3 send_ledger 2 行（每 aspect 一行）", rows("send_ledger") == 2, f"rows={rows('send_ledger')}")
    check("A4 产物齐全", all(
        (PROJ / "output" / today / "g3-a" / n).exists()
        or any((PROJ / "output" / today / "g3-a").glob(n))
        for n in ("video.mp4", "video_16_9.mp4")), "video.mp4 / video_16_9.mp4")

    # ---------------------------------------------------------------- B
    print("\n[B] 同一物料再跑一次（应命中内容幂等）")
    rc_b, out_b = run("g3-b")
    skipped = "幂等跳过" in out_b
    check("B1 命中 uq_run_content 幂等跳过", skipped)
    check("B2 未新增发送", len(SENT) == 2, f"calls={len(SENT)}")
    check("B3 send_ledger 仍 2 行", rows("send_ledger") == 2, f"rows={rows('send_ledger')}")

    # ---------------------------------------------------------------- C
    print("\n[C] 抹掉 dispatch 完成标记后重发（应命中发送台账）")
    db = PipelineDB(str(DB))
    db.set_step_status("g3-a", "dispatch", "PENDING")
    db.update_run_status("g3-a", "RUNNING")
    dispatch_json = PROJ / "output" / today / "g3-a" / "dispatch.json"
    if dispatch_json.exists():
        dispatch_json.unlink()
    rc_c, out_c = run("g3-a", ["--resume"])
    already = False
    if dispatch_json.exists():
        try:
            already = bool(json.loads(dispatch_json.read_text(encoding="utf-8")).get("already_sent"))
        except json.JSONDecodeError:
            already = False
    # 台账拦截发生在 hook.send_ledger（dispatch 的 before_hooks），比执行体更早，
    # 所以判据要说"被拦截"而不是"退出码必须是 0"——被 before_hooks 挡下时 rc=64 是正确行为。
    blocked_by_ledger = "already_sent" in out_c or already
    check("C1 命中 send_ledger already_sent", blocked_by_ledger,
          f"dispatch.json={dispatch_json.name} rc={rc_c}")
    check("C2 未新增发送", len(SENT) == 2, f"calls={len(SENT)}")
    check("C3 send_ledger 仍 2 行", rows("send_ledger") == 2, f"rows={rows('send_ledger')}")
    check("C4 重跑被台账拦截（rc=64 属预期）", rc_c in (0, 64) and blocked_by_ledger, f"rc={rc_c}")

    # ---------------------------------------------------------------- 汇总
    print("\n" + "=" * 72)
    if failures:
        print(f"GATE-3 FAIL（{len(failures)} 项）")
        for f in failures:
            print("  -", f)
        return 1
    print(f"GATE-3 PASS  同一物料 3 次执行，飞书只收到 {len(SENT)} 条（竖+横各 1），台账只 +{len(SENT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
