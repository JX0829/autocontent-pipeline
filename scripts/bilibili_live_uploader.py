#!/usr/bin/env python3
"""
scripts/bilibili_live_uploader.py
使用 bilibili_api 将视频安全上传至 Bilibili 创作者稿件箱
"""

import sys
import json
import argparse
import asyncio
from pathlib import Path
from bilibili_api import Credential, video_uploader

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--cover", required=False, default="")
    parser.add_argument("--title", required=True)
    parser.add_argument("--desc", required=True)
    parser.add_argument("--tags", required=True)
    parser.add_argument("--tid", type=int, default=231)
    args = parser.parse_args()

    cookie_file = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/cookies_bilibili.json")
    if not cookie_file.exists():
        print(json.dumps({"error": "No cookies_bilibili.json found"}))
        sys.exit(1)

    with open(cookie_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    cred = Credential(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        buvid3=cookies.get("buvid3", ""),
        dedeuserid=cookies.get("DedeUserID")
    )

    page = video_uploader.VideoUploaderPage(
        path=args.video,
        title=args.title[:80]
    )

    meta = video_uploader.VideoMeta(
        tid=args.tid,
        title=args.title[:80],
        desc=args.desc,
        cover=args.cover if args.cover and Path(args.cover).exists() else "",
        tags=args.tags.split(",") if "," in args.tags else [args.tags],
        original=True,
        no_reprint=True,  # 开启未经允许禁止转载 (1)
        recreate=False    # 严禁二创/重新上传
    )

    uploader = video_uploader.VideoUploader(
        pages=[page],
        meta=meta,
        credential=cred
    )

    res = await uploader.start()
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
