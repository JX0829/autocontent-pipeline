#!/usr/bin/env python3.14
"""自测两个审批兜底修复（不连飞书也能跑的部分 + 连飞书反查部分）。"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv
load_dotenv(PROJ / ".env")

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"PASS  {name}" + (f" | {detail}" if detail else ""))
    else:
        bad += 1
        print(f"FAIL  {name}" + (f" | {detail}" if detail else ""))


# ---------- A. resolve_chat_id 能把 open_id 模式的 message_id 反查成 chat_id ----------
from src.feishu.approval_service import FeishuApprovalService

svc = FeishuApprovalService()
chk("A0 构造 FeishuApprovalService", True, f"app_id 前 12 位={svc.app_id[:12]}")

# 用今晚真实发出的审批卡 msg_id 反查（若消息已过期会被清理，失败即 SKIP）
real_mid = os.getenv("PROBE_MSG_ID") or "om_x100b6046b012bd30df0ce9625661e96"
cid = svc.resolve_chat_id(real_mid)
chk("A1 真实 msg_id → chat_id", bool(cid), f"{real_mid} -> {cid or '(空：消息可能已被清理)'}")

chk("A2 空 msg_id 安全返回空", svc.resolve_chat_id("") == "", "无异常")
chk("A3 非法 msg_id 不抛栈", svc.resolve_chat_id("om_not_a_real_id") == "",
    "返回空字符串")

# ---------- B. 晚到裁决按 mtime 补记 ----------
from src.pipeline.executors import _adopt_late_decision
import src.approval.feishu as af

tmp = Path(tempfile.mkdtemp(prefix="late-decision-"))
run_id = "selftest-late-1"
ap = tmp / "approval.json"
ap.write_text(json.dumps({"decision": "reject", "decided_by": "feishu_timeout"}),
              encoding="utf-8")
old = time.time() - 600
os.utime(ap, (old, old))

# 把裁决文件指到临时目录，避免污染真实 data/approvals
orig = af._decision_path
af._decision_path = lambda rid: tmp / f"{rid}.json"
try:
    (tmp / f"{run_id}.json").write_text(json.dumps(
        {"decision": "approve", "by": "card:hermes", "at": int(time.time())}),
        encoding="utf-8")
    ctx = SimpleNamespace(run_id=run_id, run_root=tmp)
    _adopt_late_decision(ctx, ap)
    rec = json.loads(ap.read_text(encoding="utf-8"))
    chk("B1 晚到 approve 被采纳", rec.get("decision") == "approve", str(rec.get("decided_by")))
    chk("B2 来源照抄不伪造", rec.get("decided_by") == "feishu_file:card:hermes", rec.get("decided_by"))

    # 反向：裁决文件更旧 → 不得覆盖
    ap.write_text(json.dumps({"decision": "reject", "decided_by": "feishu_timeout"}),
                  encoding="utf-8")
    new = time.time()
    os.utime(ap, (new, new))
    older = new - 300
    os.utime(tmp / f"{run_id}.json", (older, older))
    _adopt_late_decision(SimpleNamespace(run_id=run_id, run_root=tmp), ap)
    rec2 = json.loads(ap.read_text(encoding="utf-8"))
    chk("B3 旧裁决不覆盖新 reject", rec2.get("decision") == "reject", rec2.get("decided_by"))

    # 反向：裁决文件不存在 → 不动
    (tmp / f"{run_id}.json").unlink()
    _adopt_late_decision(SimpleNamespace(run_id=run_id, run_root=tmp), ap)
    chk("B4 无裁决文件时保持原样",
        json.loads(ap.read_text(encoding="utf-8")).get("decision") == "reject")
finally:
    af._decision_path = orig

print(f"\n{'PASS' if bad == 0 else 'FAIL'}  {ok} 项通过 / {bad} 项失败")
sys.exit(0 if bad == 0 else 1)
