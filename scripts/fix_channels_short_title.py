#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
"""视频号**维护动作**：补填已发布作品的短标题（不属于发布入口）。

为什么单独成文件：`scripts/publish_channels.py` 作为「第二个发布入口」已于 2026-11-15
删除（发布统一走 `runner --publish channels`）。但 PR #55 的「补短标题」能力是**事后维护**，
既不发布也不上传，留在发布脚本里会被收口一起删掉 —— 所以搬到这里，只做这一件事。

实现在 `src/providers/channels_edit.py::fix_short_titles`（Vue enterEditPost 面板）。

用法：
  python3.14 scripts/fix_channels_short_title.py --run-id <run_id> --dry-run
  python3.14 scripts/fix_channels_short_title.py --run-id <run_id> --apply
  python3.14 scripts/fix_channels_short_title.py --all-missing --dry-run
  python3.14 scripts/fix_channels_short_title.py --match "黄仁勋辞任" --apply
  python3.14 scripts/fix_channels_short_title.py --run-id <run_id> --short-title "人工指定" --apply

退出码：0 全部 OK/DRY_RUN/ALREADY_SET；3 有失败；2 参数不全
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="视频号补短标题（维护动作，不发布）")
    ap.add_argument("--run-id", default="", help="按 run 产物定位作品")
    ap.add_argument("--all-missing", action="store_true", help="扫全量短标题为空的已发布作品")
    ap.add_argument("--match", default="", help="按描述片段定位作品（run 产物被清理时用）")
    ap.add_argument("--short-title", default="", help="人工指定短标题（覆盖自动派生）")
    ap.add_argument("--apply", action="store_true", help="真写（默认 dry-run）")
    args = ap.parse_args(argv)

    if not (args.run_id or args.all_missing or args.match):
        print("✗ 需要 --run-id <run_id> / --match <片段> / --all-missing")
        return 2

    from src.providers import channels_edit

    rep = asyncio.run(channels_edit.fix_short_titles(
        run_id=args.run_id, all_missing=bool(args.all_missing),
        dry_run=not args.apply, override=args.short_title, match=args.match))
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    bad = [r for r in rep.get("results", [])
           if r.get("result") not in ("OK", "DRY_RUN", "ALREADY_SET")]
    if rep.get("dry_run"):
        return 2 if bad else 0
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
