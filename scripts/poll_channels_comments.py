#!/usr/bin/env python3
"""
scripts/poll_channels_comments.py
微信视频号创作者平台 (channels.weixin.qq.com) 评论守护与回复建议生成 (方案 A: 人机协同闭环)
核心逻辑：
1. 复用本地持久化的 Playwright storage_state.json 会话无头访问互动管理页；
2. 提取未回复的粉丝评论（作者、评论文本、作品标题、发布时间）；
3. 经三级安全漏斗 (CommentFunnel) 由大模型拟写高情商、懂梗、合规的极客建议回复；
4. 严格遵守方案 A 规范：不执行任何 CDP 自动化点击写入（杜绝微信风控），返回结构化待办供飞书推送。
"""

import os
import sys
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.safety.comment_funnel import CommentSafetyFunnel
from src.platform_policy import get_platform_policy

logger = logging.getLogger("ChannelsCommentGuard")
DATA_DIR = PROJECT_ROOT / "data" / "browser_profiles" / "channels-tech"
STATE_PATH = DATA_DIR / "storage_state.json"
CACHE_FILE = PROJECT_ROOT / "data" / "seen_channels_comments.json"

def load_seen_comment_ids() -> set:
    if CACHE_FILE.exists():
        try:
            return set(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen_comment_ids(seen: set):
    PROJECT_ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(list(seen), ensure_ascii=False, indent=2), encoding="utf-8")

async def fetch_channels_unreplied_comments() -> List[Dict[str, Any]]:
    """无头抓取视频号未回复评论（带容错与超时保护）"""
    if not STATE_PATH.exists():
        logger.warning(f"Channels state file not found at {STATE_PATH}")
        return []

    from playwright.async_api import async_playwright

    comments = []
    seen_ids = load_seen_comment_ids()

    try:
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        launch_kwargs = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        }
        if os.path.exists(chrome_path):
            launch_kwargs["executable_path"] = chrome_path

        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                storage_state=str(STATE_PATH),
                viewport={"width": 1280, "height": 900}
            )
            page = await context.new_page()
            url = "https://channels.weixin.qq.com/platform/interaction/comment"
            logger.info(f"Navigating to WeChat Channels comment page: {url}")
            await page.goto(url, wait_until="commit", timeout=25000)
            await asyncio.sleep(6)

            # 校验登录态是否仍然有效
            if "login" in page.url:
                logger.warning("WeChat Channels session expired, redirected to login page.")
                await browser.close()
                return []

            # 抓取页面上的未回复评论列表或微前端卡片
            # 容错扫描所有 frame 和 DOM
            comment_nodes = await page.query_selector_all(".comment-item, .weui-desktop-table tr, .comment-cell, tr, [class*='comment']")
            logger.info(f"Found {len(comment_nodes)} raw comment items on page.")

            # 若未找到具体 DOM 或当前暂无未读评论，记录安全日志
            for idx, node in enumerate(comment_nodes[:10]):
                text = await node.inner_text()
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                if len(lines) >= 2:
                    cid = f"chn_{hash(text) & 0xffffffff}"
                    if cid not in seen_ids:
                        comments.append({
                            "comment_id": cid,
                            "user": lines[0][:20],
                            "content": lines[1][:150],
                            "title": lines[2][:40] if len(lines) > 2 else "视频号最新视频",
                            "raw_text": text
                        })

            await browser.close()
    except Exception as e:
        logger.error(f"Error fetching WeChat Channels comments: {e}")

    return comments

def poll_and_reply_channels_comments(dry_run: bool = True) -> List[Dict[str, Any]]:
    """
    守护总线接口：
    由于微信平台特性与方案 A 规划，此处强制以 dry_run=True (人机协同待办) 模式运行。
    生成 AI 建议回复草稿，并格式化成飞书卡片消息载荷。
    """
    logger.info("Starting WeChat Channels comment poll (Mode A: Manual Approval Relayed)...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        raw_comments = loop.run_until_complete(fetch_channels_unreplied_comments())
    finally:
        loop.close()

    if not raw_comments:
        logger.info("No new unreplied WeChat Channels comments found.")
        return []

    funnel = CommentSafetyFunnel()
    seen_ids = load_seen_comment_ids()
    records = []

    for c in raw_comments:
        cid = c["comment_id"]
        user = c["user"]
        content = c["content"]
        video_title = c.get("title", "最新视频")

        # 调用三级安全漏斗审查与回复生成
        fact_summary = f"微信视频号粉丝互动：关于《{video_title}》的技术探讨。源码与工程实践，欢迎在评论区交流。"
        try:
            funnel_res = funnel.process_comment(
                raw_comment=content,
                fact_summary_150=fact_summary,
                resource_keyword="channels",
                private_domain_hook="欢迎在评论区交流讨论～"
            )
            reply_draft = (funnel_res.get("reply") or "").strip()
        except Exception as e:
            logger.error(f"Error during funnel evaluation: {e}")
            reply_draft = f"感谢关注与探讨！《{video_title}》更多工程细节欢迎在评论区留言交流～"

        if not reply_draft:
            reply_draft = f"收到探讨！针对《{video_title}》的工程落地，大家觉得还有哪些可以优化的地方？"

        record = {
            "platform": "channels",
            "video_title": video_title,
            "user": user,
            "comment": content,
            "reply": reply_draft,
            "reply_mode": "manual_approval",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        records.append(record)
        seen_ids.add(cid)
        logger.info(f"Generated draft reply for Channels user @{user}: '{reply_draft}'")

    save_seen_comment_ids(seen_ids)
    return records

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = poll_and_reply_channels_comments(dry_run=True)
    print(f"Total processed: {len(res)}")
    for r in res:
        print(r)
