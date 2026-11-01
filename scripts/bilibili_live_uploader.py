#!/usr/bin/env python3
"""
scripts/bilibili_live_uploader.py
使用 bilibili_api 将视频安全上传至 Bilibili 创作者稿件箱
支持显式注入：
1. neutral_mark: "含AI生成内容"
2. creation_statement_id: 1 (含AI生成内容)
3. no_reprint: 1 (未经允许禁止转载)
4. is_only_self: 1 (仅自己可见/私密)
"""

import sys
import json
import argparse
import asyncio
from pathlib import Path
from copy import copy, deepcopy
from bilibili_api import Credential, video_uploader

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--cover", required=False, default="")
    parser.add_argument("--title", required=True)
    parser.add_argument("--desc", required=True)
    parser.add_argument("--tags", required=True)
    parser.add_argument("--tid", type=int, default=231)
    parser.add_argument("--only-self", action="store_true", default=False)
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
        no_reprint=True,
        recreate=False,
        neutral_mark="含AI生成内容"
    )

    uploader = video_uploader.VideoUploader(
        pages=[page],
        meta=meta,
        credential=cred
    )

    # 猴子补丁 _submit，精准注入 creation_statement 与 is_only_self
    orig_submit = uploader._submit
    async def patched_submit(videos: list, cover_url: str = "") -> dict:
        meta_dict = copy(uploader.meta.__dict__() if isinstance(uploader.meta, video_uploader.VideoMeta) else uploader.meta)
        meta_dict["cover"] = cover_url
        meta_dict["videos"] = videos
        meta_dict["csrf"] = cred.bili_jct
        
        # 核心合规参数：AIGC 官方创作声明与私密控制
        meta_dict["neutral_mark"] = "作者声明：该视频使用人工智能合成技术"
        meta_dict["creation_statement_id"] = 1
        meta_dict["no_reprint"] = 1
        if args.only_self:
            meta_dict["is_only_self"] = 1
            meta_dict["space_hidden"] = 2  # 空间隐藏

        params = {"csrf": cred.bili_jct, "t": asyncio.get_event_loop().time() * 1000}
        api = video_uploader._API["submit"]
        resp = (
            await video_uploader.Api(
                **api, credential=cred, no_csrf=True, json_body=True
            )
            .update_params(**params)
            .update_data(**meta_dict)
            .result
        )
        return resp

    uploader._submit = patched_submit
    res = await uploader.start()
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
