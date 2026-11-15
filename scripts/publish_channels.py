#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""微信视频号发布 —— 薄壳 CLI

**唯一发布实现在 `src/pipeline/executors._invoke_provider`**，本文件只保留运维动作：
`--probe`（探页面控件与登录态）、`--dry-run`（校验不真发）、`--stop-before-publish`
（停在发表前核选择器）、台账查重与结果回写。

2026-11-11 收敛原因：这里原先另有一套 `compose_caption_body()`，末尾写死了一句
「它不接管整个项目，只把流程拆成可复用的几步」——跟内容无关的模板尾巴，而且
`load_run()` 算完就丢、从未被使用。结果同一条视频走 CLI（video_desc，213 字）和走
pipeline（narration[:180]，158 字）发出去的简介不是一回事。现在文案与发布都走
项目自身链路，回执卡也只有一份实现（`src/feishu/card_builder`）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJ / ".env")

from src.pipeline.executors import _invoke_provider, send_publish_receipt  # noqa: E402
from src.providers.channels_cdp import ChannelsCDPProvider  # noqa: E402

ACCOUNT = {"id": "channels-tech-main", "platform": "channels",
           "publish_mode": "public", "enabled": True,
           "profile_path": "./data/browser_profiles/channels-tech"}


def load_run(run_id: str) -> tuple[Path, Path, dict, Path]:
    """按 run_id 找竖版视频/封面/draft（视频号只收 9:16）。"""
    hits = sorted(PROJ.glob(f"output/*/{run_id}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        raise SystemExit(f"❌ 找不到 run: {run_id}")
    d = hits[0]
    video, cover = d / "video.mp4", d / "cover.png"
    if not video.exists():
        raise SystemExit(f"❌ 缺竖版视频: {video}")
    dp = d / "draft_script.json"
    draft = json.loads(dp.read_text(encoding="utf-8")) if dp.exists() else {}
    return video, cover, draft, d


def main() -> int:
    ap = argparse.ArgumentParser(description="微信视频号发布（薄壳：实现在 pipeline）")
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
    ap.add_argument("--headful", action="store_true",
                    help="（已默认有头，此开关仅为兼容保留；视频号不允许无头）")
    ap.add_argument("--headless", action="store_true",
                    help="无头模式（不推荐：视频号会被风控/登录态校验打回；仅排障用）")
    ap.add_argument("--stop-before-publish", action="store_true",
                    help="上传+填文案+声明原创后停在发表前（测试选择器用，不产生公开内容）")
    ap.add_argument("--no-receipt", action="store_true", help="不发发布回执卡")
    ap.add_argument("--force", action="store_true",
                    help="确认要走单平台入口（默认拒绝，统一用 runner --publish）")
    ap.add_argument("--fix-short-title", action="store_true",
                    help="补填已发布作品的短标题（SPEC-20261115）；配合 --run-id 或 --all-missing")
    ap.add_argument("--all-missing", action="store_true",
                    help="配合 --fix-short-title：扫全量短标题为空的已发布作品")
    ap.add_argument("--match", default="",
                    help="配合 --fix-short-title：按描述片段定位作品（run 产物被清理时用）")
    ap.add_argument("--short-title", default="",
                    help="配合 --fix-short-title：人工指定短标题（覆盖自动派生）")
    args = ap.parse_args()

    if args.fix_short_title:
        import asyncio
        from src.providers import channels_edit

        if not (args.run_id or args.all_missing or args.match):
            print("✗ --fix-short-title 需要 --run-id <run_id> / --match <片段> / --all-missing")
            return 2
        rep = asyncio.run(channels_edit.fix_short_titles(
            run_id=args.run_id or "", all_missing=bool(args.all_missing),
            dry_run=bool(args.dry_run), override=args.short_title,
            match=args.match))
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        bad = [r for r in rep.get("results", []) if r.get("result") not in ("OK", "DRY_RUN", "ALREADY_SET")]
        return 0 if not bad else 3 if not rep.get("dry_run") else 2

    if args.probe:
        res = ChannelsCDPProvider(ACCOUNT).publish_live(
            video_path="", cover_path="", title="", content_text="", topics=[],
            dry_run=True, headless=bool(args.headless))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("status") == "DRY_RUN_OK" else 2

    # 单平台入口已收口到流水线：默认拒绝，避免绕过审批/台账/回执卡三条链路
    if not (args.force or args.dry_run or args.stop_before_publish):
        print("⚠️  单平台发布入口已废弃，统一走流水线一条命令：")
        print("   python3 -m src.pipeline.runner --material materials/<物料>.json \\")
        print("           --publish douyin,bilibili,channels")
        print("   排障确实需要单平台时加 --force 重跑（--probe / --dry-run / --stop-before-publish 不受影响）。")
        return 2

    # ---- 输入 ----
    if args.video:
        video, cover = Path(args.video), Path("")
        draft = {"title": args.title, "video_desc": args.body, "tags": list(args.tags)}
        run_root = None
        title = args.title
    elif args.run_id:
        video, cover, draft, run_root = load_run(args.run_id)
        title = str(draft.get("title") or draft.get("short_title") or args.run_id)[:30]
    else:
        ap.error("需要 --run-id 或 --video")

    print(f"视频: {video}\n标题: {title}\n话题: {draft.get('tags') or []}")

    # ---- 台账查重（probe / dry-run / stop-before-publish 不占位）----
    led_key = ""
    if not (args.dry_run or args.stop_before_publish):
        from src.publish_ledger import PublishBlocked, check_and_claim
        try:
            led_key, _info = check_and_claim("channels", ACCOUNT["id"], str(video), title)
        except PublishBlocked as exc:
            print(f"⛔ 发布前查重拦截：{exc}")
            return 3

    # ---- 发布：走 pipeline 的唯一实现 ----
    res = _invoke_provider(
        "channels", ACCOUNT, video, cover, draft, dry_run=args.dry_run,
        run_root=run_root,
        opts={"headless": bool(args.headless),
              "stop_before_publish": args.stop_before_publish,
              "allow_missing_flags": args.allow_missing_flags,
              "declare_original": not args.no_original,
              "declare_aigc": not args.no_aigc})
    print(json.dumps(res, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0 if res.get("status") == "DRY_RUN_OK" else 2

    if led_key:
        from src.publish_ledger import record_outcome
        # 视频号 status 决定能不能重发：
        #   PUBLISHED_UNVERIFIED / 未知 → unverified（可能已发出，禁止重发）
        #   ORIGINAL_CHECKBOX_NOT_FOUND / LOGIN_EXPIRED → failed（确定没提交，可重试）
        state = record_outcome(
            led_key, bool(res.get("published")),
            external_id=str(res.get("post_id") or res.get("msg_id") or ""),
            status=str(res.get("status") or ""),
            reason=str(res.get("reason") or res.get("message") or ""))
        print(json.dumps({"ledger": state}, ensure_ascii=False))

    # ---- 回执 / 明细 / 卡片：与流水线同一张卡 ----
    # 走 publish_report 合并进该 run 的 publish.json 并**原地更新审批卡**，
    # 不再另发一张 1/1 的回执（那样一个 run 会有两张卡、各说一半）。
    if not (args.no_receipt or args.stop_before_publish):
        item = {"platform": "channels", "bucket": "published" if res.get("published") else "failed",
                "msg_id": str(res.get("post_id") or res.get("msg_id") or ""),
                "ledger": "published" if res.get("published") else "failed",
                "status": res.get("status") or "", "aspect": "vertical",
                "video": video.name, "cover": cover.name if cover else "",
                "source": "single_platform_cli"}
        if not res.get("published"):
            item["error"] = str(res.get("status") or res.get("message") or "失败")
        if run_root is not None:
            from src.publish_report import merge_and_finalize
            _rep = merge_and_finalize(Path(run_root), [item], title)
            print(f"  ✓ 已合并进 run 的发布结论：{_rep.get('receipt')}")
        else:
            # 纯 CLI（没有 run 目录）才发独立回执，保证结果仍可见
            from src.publish_report import merge_results, send_receipt_card
            _rep = merge_results({}, [item])
            _rep["receipt"] = None
            send_receipt_card(_rep, title)

    return 0 if res.get("published") else 1


if __name__ == "__main__":
    sys.exit(main())
