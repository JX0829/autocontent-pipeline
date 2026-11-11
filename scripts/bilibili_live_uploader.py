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

PROJ = Path(__file__).resolve().parent.parent
# provider 走的是 `--video/--title/--desc/--tags` 形态（不是 --run-id）：
# 此时 sys.path[0] 是 scripts/，`from src.publish_ledger import ...` 必崩
# ModuleNotFoundError。路径必须插在模块头，不能只放在 --run-id 分支里。
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))


def _load_bilibili_api():
    """延迟导入 bilibili_api：缺依赖时给可读错误 + 约定退出码，而不是裸栈。

    该包不在项目解释器里（PEP668 锁定），只有 bili-cli 的 venv 装了它。
    原先在模块头 import，任何解释器不对都会在参数校验之前崩掉。
    """
    try:
        from bilibili_api import Credential, video_uploader
    except ModuleNotFoundError as exc:
        print(json.dumps({
            "error": f"缺少依赖 {exc.name}（当前解释器 {sys.executable}）",
            "hint": "用装了 bilibili-api-python 的解释器运行；或设 BILI_PYTHON 指向它，"
                    "或 uv pip install bilibili-api-python",
        }, ensure_ascii=False))
        sys.exit(2)
    return Credential, video_uploader


def _norm_title(raw: str) -> tuple[str, str]:
    """标题边界归一化：纯空格/全角空格不是有效标题（平台侧报 21009 标题不合法）。

    返回 (标题, 警告)；警告非空要打到 stdout，不能静默截断。
    """
    title = str(raw or "").strip()
    warn = ""
    if len(title) > 80:
        warn = f"标题 {len(title)} 字超过 B 站上限 80，已截断"
        title = title[:80]
    return title, warn


def missing_probe(args) -> bool:
    """参数不全时不查重（先让缺参数报错更直观）。"""
    return not (getattr(args, "video", "") and getattr(args, "title", ""))


async def main():
    Credential, video_uploader = _load_bilibili_api()
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=False, default="")
    parser.add_argument("--cover", required=False, default="")
    parser.add_argument("--title", required=False, default="")
    parser.add_argument("--desc", required=False, default="")
    parser.add_argument("--tags", required=False, default="")
    parser.add_argument("--run-id", required=False, default="",
                        help="从 output/*/<run_id>/ 自动取横版视频、标题、人话简介与标签（推荐）")
    parser.add_argument("--tid", type=int, default=231)
    parser.add_argument("--only-self", action="store_true", default=False)
    args = parser.parse_args()

    run_root = None
    # --run-id 模式：文案统一走 src/utils/video_desc，避免各平台各写一套
    if args.run_id:
        hits = sorted(PROJ.glob(f"output/*/{args.run_id}/video_16_9.mp4"), key=lambda p: p.stat().st_mtime)
        if not hits:
            print(json.dumps({"error": f"找不到 run '{args.run_id}' 的 video_16_9.mp4"}))
            sys.exit(2)
        run_root = hits[-1].parent
        args.video = args.video or str(hits[-1])
        args.cover = args.cover or str(run_root / "cover_16_9.png")
        draft = json.loads((run_root / "draft_script.json").read_text(encoding="utf-8"))
        from src.utils.video_desc import build_desc
        args.title = args.title or str(draft.get("title") or "")[:80]
        args.desc = args.desc or build_desc(draft, platform="bilibili", run_root=run_root)
        if not args.tags:
            args.tags = ",".join(str(t) for t in (draft.get("tags") or []))

    # 参数与登录态校验放在查重之前：确定发不出去的错误不该占用台账位
    # 标题先归一化：`"   "` / `"\u3000"` 也是无效标题，平台侧只回 21009（看不懂）
    args.title, title_warn = _norm_title(args.title)
    if title_warn:
        print(json.dumps({"warning": title_warn}, ensure_ascii=False))
    missing = [k for k in ("video", "title", "desc", "tags") if not str(getattr(args, k) or "").strip()]
    if missing:
        print(json.dumps({"error": f"缺少参数: {', '.join(missing)}（或使用 --run-id）"},
                         ensure_ascii=False))
        sys.exit(2)
    if not Path(args.video).exists():
        print(json.dumps({"error": f"视频不存在: {args.video}"}, ensure_ascii=False))
        sys.exit(2)

    cookie_file = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/cookies_bilibili.json")
    if not cookie_file.exists():
        print(json.dumps({"error": "No cookies_bilibili.json found"}))
        sys.exit(1)

    # 发布前查重：同一视频在同一账号已发过就不再发（不可撤回的公开内容）
    led_key = ""
    if not missing_probe(args):
        from src.publish_ledger import PublishBlocked, check_and_claim
        try:
            acct = "bilibili-main"
            try:
                from src.utils.accounts import for_platform
                acct = str(for_platform("bilibili").get("id") or acct)
            except Exception:  # noqa: BLE001 — 账号配置缺失不阻塞查重，用兜底 id
                pass
            led_key, _info = check_and_claim("bilibili", acct, Path(args.video), args.title)
        except PublishBlocked as exc:
            print(json.dumps({"blocked": str(exc)}, ensure_ascii=False))
            sys.exit(3)

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

    # ── 作者首评（seed_comment）────────────────────────────────────────────
    # 配置在 config/accounts.yaml 的 seed_comment 块；接口偶发 -404（稿件刚落库）需重试
    try:
        aid = None
        if isinstance(res, dict):
            aid = res.get("aid") or (res.get("data") or {}).get("aid")
        seed_enabled, seed_pin, seed_text = (False, False, "")
        if args.run_id and run_root:
            sys.path.insert(0, "/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
            import yaml
            from src.utils.seed_comment import resolve
            cfg_path = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/config/accounts.yaml")
            accounts = (yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}).get("accounts", [])
            acc = next((a for a in accounts if a.get("platform") == "bilibili" and a.get("id") == "bilibili-tech-main"), {})
            draft_path = run_root / "draft_script.json"
            draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
            seed_enabled, seed_pin, seed_text = resolve(acc, draft, platform="bilibili")

        if seed_enabled and aid:
            from src.providers.bilibili_comment import BilibiliCommentClient
            client = BilibiliCommentClient()
            post = client.post_and_top_comment(int(aid), seed_text) if seed_pin else client.add_top_level_comment(int(aid), seed_text)
            rpid = post.get("rpid")
            if seed_pin and rpid and not post.get("top_success"):
                import time as _t
                for i in range(3):
                    _t.sleep(15)
                    top = client.top_comment(int(aid), int(rpid), 1)
                    if top.get("success"):
                        post["top_success"] = True
                        post["top_attempts"] = i + 1
                        break
            res = {"upload": res, "seed_comment": post} if isinstance(res, dict) else {"upload": res, "seed_comment": post}
    except Exception as exc:  # noqa: BLE001 - 首评失败不能吞掉上传结果，但必须可见
        logger_msg = f"SEED_COMMENT_FAILED: {exc}"
        print(json.dumps({"seed_comment_error": logger_msg}, ensure_ascii=False))

    if led_key:
        from src.publish_ledger import record_outcome
        bvid = ""
        ok = isinstance(res, dict) and not res.get("error")
        if ok:
            # 台账要留**可核对**的凭证：上传接口的返回值常常没有 bvid，
            # 所以去稿件列表按标题回查（稿件刚落库会有几秒延迟，重试 3 次）。
            bvid = str(res.get("bvid") or "") if isinstance(res, dict) else ""
            if not bvid:
                from src.publish_evidence import resolve_bvid_by_title
                bvid = resolve_bvid_by_title(args.title, cookies)
            state = record_outcome(led_key, True, external_id=bvid)
        else:
            err = str((res or {}).get("error") or "unknown") if isinstance(res, dict) else "unknown"
            # 只有"确定没提交"的错误才允许重试（status 用 SAFE_RETRY_SIGNALS 里的标记）
            if "缺少参数" in err:
                state = record_outcome(led_key, False, status="MISSING_PARAM", reason=err)
            else:
                # 提交后返回 error / 结构异常 → 平台可能已收到，禁止自动重发
                state = record_outcome(led_key, False, status="SUBMIT_UNKNOWN", reason=err)
        print(json.dumps({"ledger": state, "bvid": bvid if ok else ""}, ensure_ascii=False))
    print(json.dumps(res, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
