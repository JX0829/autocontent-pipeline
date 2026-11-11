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


def compose_caption_body(draft: dict) -> str:
    """视频号简介正文：把分镜旁白提炼成 ①②③ 要点块（不含标题与话题，由 provider 拼）"""
    scenes = draft.get("scenes") or []
    bullets = []
    for sc in scenes:
        t = str(sc.get("segment_narration") or "").strip()
        if not t:
            continue
        # 末镜常带「欢迎在评论区交流讨论～」这类收尾，不放进要点
        if "欢迎在评论区" in t or "评论区交流" in t:
            continue
        bullets.append(t)
    bullets = bullets[:3]
    fact = str(draft.get("fact_summary_150") or "").strip()
    lines = []
    if fact:
        lines.append(fact)
    if bullets:
        if lines:
            lines.append("")
        marks = "①②③④⑤"
        for i, b in enumerate(bullets):
            lines.append(f"{marks[i]} {b}")
    lines.append("")
    lines.append("它不接管整个项目，只把流程拆成可复用的几步；欢迎在评论区交流讨论～")
    return "\n".join(lines).strip()


def build_body(draft: dict) -> str:
    """简介正文：优先 full_body/summary，其次 150 字事实摘要，最后用分镜旁白拼要点。

    历史坑：draft_script.json 只产出 fact_summary_150 + scenes[].segment_narration，
    没有 full_body/summary，旧实现直接拿到空串 → 视频号发出「只有标题没正文」的空简介。
    """
    for key in ("full_body", "summary", "fact_summary_150"):
        v = draft.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    narr = [str(s.get("segment_narration") or "").strip()
            for s in (draft.get("scenes") or [])]
    narr = [n for n in narr if n]
    if not narr:
        return ""
    lines, buf = [], ""
    for n in narr[:3]:
        cand = (buf + " " + n).strip() if buf else n
        if len(cand) > 160 and buf:
            lines.append(buf)
            buf = n
        else:
            buf = cand
    if buf:
        lines.append(buf)
    return "\n".join(lines).strip()


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
    body = compose_caption_body(draft) or build_body(draft)
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
        dp = sorted(PROJ.glob(f"output/*/{args.run_id}/draft_script.json"))
        if dp:
            d = json.loads(dp[0].read_text(encoding="utf-8"))
            from src.utils.video_desc import build_desc
            body = build_desc(d, tags=tags, platform="channels", run_root=dp[0].parent,
                              include_tags=False)
        else:
            body = ""
    else:
        ap.error("需要 --run-id 或 --video")

    print(f"视频: {video}\n标题: {title}\n话题: {tags}")

    # 发布前查重（probe / dry-run / stop-before-publish 不占位、不查重）
    led_key = ""
    if not (args.dry_run or args.stop_before_publish):
        from src.publish_ledger import PublishBlocked, check_and_claim, mark_failed, mark_published
        try:
            led_key, _info = check_and_claim("channels", ACCOUNT["id"], video, title)
        except PublishBlocked as exc:
            print(f"⛔ 发布前查重拦截：{exc}")
            return 3

    res = p.publish_live(
        video_path=video, cover_path=cover, title=title, content_text=body,
        topics=list(tags), declare_original=not args.no_original,
        declare_aigc=not args.no_aigc, dry_run=args.dry_run,
        allow_missing_flags=args.allow_missing_flags, headless=not args.headful,
        stop_before_publish=args.stop_before_publish)
    print(json.dumps(res, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0 if res.get("status") == "DRY_RUN_OK" else 2
    if led_key:
        from src.publish_ledger import record_outcome
        # 视频号的 status 决定能不能重发：
        #   PUBLISHED_UNVERIFIED / 未知 → unverified（可能已发出，禁止重发）
        #   ORIGINAL_CHECKBOX_NOT_FOUND / LOGIN_EXPIRED → failed（确定没提交，可重试）
        state = record_outcome(
            led_key, bool(res.get("published")),
            external_id=str(res.get("post_id") or res.get("msg_id") or ""),
            status=str(res.get("status") or ""),
            reason=str(res.get("reason") or res.get("message") or ""))
        print(json.dumps({"ledger": state}, ensure_ascii=False))
    return 0 if res.get("published") else 1


if __name__ == "__main__":
    sys.exit(main())
