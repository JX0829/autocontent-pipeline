#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给已存在的 run 重发一张审批卡（不跑流水线、不重渲染）。

用途：卡片被删/发错了/正文来源已修好，需要重新要一次裁决。

用法:
    python3 scripts/resend_approval_card.py <run_id>          # dry-run：只构建并打印
    python3 scripts/resend_approval_card.py <run_id> --send   # 真发（写 approval_card.json）

安全性：
  · run_id 必须匹配 [A-Za-z0-9._-]+（防路径穿越拼进 glob）；
  · 标题或正文为空、卡片没有按钮 → 拒绝发送；
  · 真发前把旧裁决归档为 .bak.<ts>，保证新点击是唯一有效裁决。
退出码：0 成功 / 2 找不到 run / 3 标题或正文为空 / 4 无按钮 / 5 发卡失败
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def load_env() -> None:
    env = PROJ / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run_id = sys.argv[1]
    if not RUN_ID_RE.match(run_id):
        print(f"FATAL: 非法 run_id {run_id!r}（只允许字母/数字/._-）")
        return 2
    do_send = "--send" in sys.argv
    load_env()

    from src.approval.feishu import _publish_targets
    from src.approval.styles import get_style
    from src.utils.video_desc import build_desc

    hits = sorted((PROJ / "output").glob(f"*/{run_id}"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        print(f"FATAL: 找不到 run 目录 output/*/{run_id}")
        return 2
    run_dir = hits[0]

    draft = json.loads((run_dir / "draft_script.json").read_text(encoding="utf-8"))
    title = str(draft.get("title") or draft.get("short_title") or "")
    desc = build_desc(draft, platform="channels")
    targets = _publish_targets()

    card = get_style("approval_v2")(
        run_id=run_id, title=title, targets=targets, delivered=True,
        header_template="blue", show_publish_matrix=True, desc=desc)

    btns: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("tag") == "button":
                btns.append({"text": (node.get("text") or {}).get("content"),
                             "type": node.get("type"),
                             "value": (node.get("behaviors") or [{}])[0].get("value")})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card)

    print(f"run_dir      : {run_dir}")
    print(f"title        : {title!r}")
    print(f"desc({len(desc)}) : {desc[:160]}...")
    print(f"targets({len(targets)}): {[(t.get('provider') or t.get('platform'), t.get('account'), t.get('aspect')) for t in targets]}")
    print(f"card header  : {((card.get('header') or {}).get('title') or {}).get('content')!r}")
    print(f"buttons({len(btns)}):")
    for b in btns:
        print(f"  - {b['text']} [{b['type']}] value={b['value']}")
    print(f"card bytes   : {len(json.dumps(card, ensure_ascii=False).encode())}")

    if not title or not desc:
        print("FATAL: 标题或正文为空，拒绝发送")
        return 3
    if not btns:
        print("FATAL: 卡片里没有按钮")
        return 4

    if not do_send:
        print("\n[DRY-RUN] 未发送。加 --send 真发。")
        return 0

    # 1) 归档旧裁决，让新点击成为唯一有效裁决
    dp = PROJ / "data" / "approvals" / f"{run_id}.json"
    if dp.exists():
        bak = dp.with_suffix(f".json.bak.{int(time.time())}")
        dp.replace(bak)
        print(f"[archived] 旧裁决 → {bak.name}")

    # 2) 发卡
    from src.feishu.approval_service import FeishuApprovalService

    receive_id = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not receive_id:
        print("FATAL: 未配置 FEISHU_APPROVAL_CHAT_ID")
        return 5
    rec_type = "open_id" if receive_id.startswith("ou_") else "chat_id"
    msg_id = FeishuApprovalService().send_approval_card(
        receive_id=receive_id, receive_id_type=rec_type, card_content=card)
    if not msg_id:
        print("FATAL: 发卡失败")
        return 5

    # 3) 覆盖 approval_card.json，保证后续 resume 复用这张新卡
    rec = run_dir / "approval_card.json"
    rec.write_text(json.dumps({"run_id": run_id, "msg_id": msg_id,
                               "chat_id": receive_id if rec_type == "chat_id" else "",
                               "at": int(time.time())}, ensure_ascii=False),
                   encoding="utf-8")
    print(f"[sent] msg_id={msg_id} → {rec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
