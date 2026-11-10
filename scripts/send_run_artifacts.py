#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把某个 run 的全部成片交付到飞书（output_only 之后的收口动作）

存在意义：`DISPATCH_MODE=output_only` 只出片不发送，交付方需要自己发文件。
人工/Agent 手工从目录里挑文件极易错配 —— 实际发生过「发了竖版封面 + 横版视频，
漏了横版封面 + 竖版视频」。本脚本把交付契约固化：4 个文件缺一个就拒绝发送。

用法:
  python3 scripts/send_run_artifacts.py --run-id <id>              # 发送全部 4 件
  python3 scripts/send_run_artifacts.py --run-id <id> --dry-run    # 只打印计划
  python3 scripts/send_run_artifacts.py --run-id <id> --videos-only # 只发 2 条视频
  python3 scripts/send_run_artifacts.py --run-id <id> --to ou_xxx   # 指定接收人

退出码: 0 全部成功 / 1 缺产物或发送失败 / 2 参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
os.chdir(PROJ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJ / ".env")

# (标签, 视频文件名, 封面文件名)
ASPECTS = [
    ("竖版 1080x1920", "video.mp4", "cover.png"),
    ("横版 1920x1080", "video_16_9.mp4", "cover_16_9.png"),
]


def find_run_dir(run_id: str) -> Path | None:
    hits = sorted(PROJ.glob(f"output/*/{run_id}"), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser(description="交付 run 成片到飞书")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--to", default=None, help="接收人 open_id / chat_id（默认 FEISHU_APPROVAL_CHAT_ID）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不发送")
    ap.add_argument("--videos-only", action="store_true", help="只发视频，不发封面大图")
    args = ap.parse_args()

    run_dir = find_run_dir(args.run_id)
    if not run_dir:
        print(f"❌ 找不到 run 目录: output/*/{args.run_id}")
        return 2

    # ---- 交付契约：4 件齐全才发 ----
    plan = []
    missing = []
    for label, vname, cname in ASPECTS:
        v = run_dir / vname
        c = run_dir / cname
        if not v.exists():
            missing.append(f"{label} 视频 {vname}")
        if not c.exists():
            missing.append(f"{label} 封面 {cname}")
        plan.append((label, v, c))

    if missing:
        print("❌ 产物不全，拒绝发送（避免发出错配组合）：")
        for m in missing:
            print(f"   · 缺 {m}")
        print(f"   目录: {run_dir}")
        return 1

    receive_id = args.to or os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not receive_id:
        print("❌ 缺 FEISHU_APPROVAL_CHAT_ID，也没有 --to")
        return 2
    rec_type = "open_id" if receive_id.startswith("ou_") else "chat_id"

    print(f"run_dir : {run_dir}")
    print(f"接收方  : {receive_id} ({rec_type})")
    for label, v, c in plan:
        size = v.stat().st_size / 1024 / 1024
        print(f"  · {label}: {v.name} ({size:.2f} MB) + {c.name}")
    if args.videos_only:
        print("  · 封面大图: 跳过（--videos-only）")

    if args.dry_run:
        print("\nDRY RUN —— 未发送任何消息")
        return 0

    from src.feishu.approval_service import FeishuApprovalService

    svc = FeishuApprovalService()
    results = []
    for label, v, c in plan:
        mid = svc.upload_and_send_video(
            receive_id=receive_id, receive_id_type=rec_type,
            video_path=str(v), content_hash=run_dir.name, task_id=run_dir.name,
            cover_path=str(c))
        print(f"  {'✅' if mid else '❌'} {label} 视频  {mid or ''}")
        results.append({"kind": "video", "aspect": label, "file": v.name, "msg_id": mid})

        if not args.videos_only:
            imid = svc.send_image(receive_id=receive_id, receive_id_type=rec_type, image_path=str(c))
            print(f"  {'✅' if imid else '❌'} {label} 封面  {imid or ''}")
            results.append({"kind": "cover", "aspect": label, "file": c.name, "msg_id": imid})

    ok = all(r["msg_id"] for r in results)
    out = run_dir / "delivery.json"
    out.write_text(json.dumps({"run_id": args.run_id, "results": results,
                               "all_sent": ok}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\n{'✅ 全部送达' if ok else '❌ 有失败项'} · 回执: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
