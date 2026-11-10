#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号发布 / 探针（真实 CDP，公开可见 + 原创声明 + AI 声明）

用法:
  # 1) 先确认登录态与页面控件（不上传、不发布）
  python3 scripts/publish_channels.py --probe

  # 2) 干跑：校验输入与登录，不上传
  python3 scripts/publish_channels.py --run-id <id> --dry-run

  # 3) 真实发布
  python3 scripts/publish_channels.py --run-id <id>

  # 4) 严格模式：原创控件找不到就中止（默认已开启）
  python3 scripts/publish_channels.py --run-id <id> --allow-missing-flags   # 放宽

退出码: 0 已发布并验证 / 1 未发布或未验证 / 2 参数或登录问题
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

from src.providers.channels_cdp import ChannelsCDPProvider  # noqa: E402

ACCOUNT = {"id": "channels-tech-main", "platform": "channels",
           "publish_mode": "public", "enabled": True,
           "profile_path": "./data/browser_profiles/channels-tech"}


def load_run(run_id: str) -> tuple[str, str, str, list[str]]:
    hits = sorted(PROJ.glob(f"output/*/{run_id}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        raise SystemExit(f"❌ 找不到 run: {run_id}")
    d = hits[0]
    video = d / "video.mp4"          # 视频号只收竖版 9:16
    cover = d / "cover.png"
    if not video.exists():
        raise SystemExit(f"❌ 缺竖版视频: {video}")
    draft = {}
    dp = d / "draft_script.json"
    if dp.exists():
        draft = json.loads(dp.read_text(encoding="utf-8"))
    title = (draft.get("title") or draft.get("short_title") or run_id)[:30]
    body = draft.get("full_body") or draft.get("summary") or ""
    tags = draft.get("tags") or draft.get("topics") or []
    return str(video), str(cover), title, tags if isinstance(tags, list) else []


def main() -> int:
    ap = argparse.ArgumentParser(description="微信视频号真实发布")
    ap.add_argument("--run-id", help="从 output/*/<run_id>/ 取竖版视频与文案")
    ap.add_argument("--video", help="直接指定视频路径")
    ap.add_argument("--title", default="", help="标题（配合 --video）")
    ap.add_argument("--body", default="", help="正文（配合 --video）")
    ap.add_argument("--tags", nargs="*", default=[])
    ap.add_argument("--probe", action="store_true", help="只探测页面控件与登录态")
    ap.add_argument("--dry-run", action="store_true", help="校验输入与登录，不上传")
    ap.add_argument("--allow-missing-flags", action="store_true",
                    help="原创控件找不到时仍继续（默认中止，fail-closed）")
    ap.add_argument("--no-original", action="store_true", help="不勾原创")
    ap.add_argument("--no-aigc", action="store_true", help="不勾 AI 声明")
    ap.add_argument("--headful", action="store_true", help="有头模式（排障用）")
    ap.add_argument("--stop-before-publish", action="store_true",
                    help="上传+填文案+声明原创后停在发表前（测试选择器用，不产生公开内容）")
    args = ap.parse_args()

    p = ChannelsCDPProvider(ACCOUNT)

    if args.probe:
        res = p.publish_live(video_path="", cover_path="", title="", content_text="",
                             topics=[], dry_run=True, headless=not args.headful)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") == "DRY_RUN_OK" else 2

    if args.video:
        video, cover, title, tags = args.video, "", args.title, args.tags
        body = args.body
    elif args.run_id:
        video, cover, title, tags = load_run(args.run_id)
        body = ""
        dp = sorted(PROJ.glob(f"output/*/{args.run_id}/draft_script.json"))
        if dp:
            d = json.loads(dp[0].read_text(encoding="utf-8"))
            body = d.get("full_body") or d.get("summary") or ""
    else:
        ap.error("需要 --run-id 或 --video")

    print(f"视频: {video}\n标题: {title}\n话题: {tags}")
    res = p.publish_live(
        video_path=video, cover_path=cover, title=title, content_text=body,
        topics=list(tags), declare_original=not args.no_original,
        declare_aigc=not args.no_aigc, dry_run=args.dry_run,
        allow_missing_flags=args.allow_missing_flags, headless=not args.headful,
        stop_before_publish=args.stop_before_publish)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0 if res.get("status") == "DRY_RUN_OK" else 2
    return 0 if res.get("published") else 1


if __name__ == "__main__":
    sys.exit(main())
