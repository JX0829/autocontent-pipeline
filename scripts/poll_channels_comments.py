#!/usr/bin/env python3
"""
scripts/poll_channels_comments.py
微信视频号创作者平台 (channels.weixin.qq.com) 评论守护与回复建议生成 (方案 A: 人机协同闭环)

核心逻辑：
1. **复用常驻 Chrome（CDP 9334）** 访问互动管理页（2026-11-22 常驻方案改造，
   不再每次 `chromium.launch()` 起临时 headless profile —— 那会每 15 分钟新起一个
   Chrome，曾堆积 14 个 puppeteer 进程把 load 拖到 155，进而拖垮飞书长连接）；
2. 提取未回复的粉丝评论（作者、评论文本、作品标题、发布时间）；
3. 经三级安全漏斗 (CommentFunnel) 由大模型拟写高情商、懂梗、合规的极客建议回复；
4. 严格遵守方案 A 规范：不执行任何 CDP 自动化点击写入（杜绝微信风控），返回结构化待办供飞书推送。

常驻方案铁律：
    · 只开 tab、读完 `page.close()`，**绝不 `browser.close()` 关掉常驻进程**；
    · tab 超过 `CHANNELS_MAX_TABS`（默认 5）时关最旧的，不碰进程；
    · 登录态在磁盘 profile 里，关 tab / 关进程都不会掉登录。
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
from src.providers.channels_session import ensure as ensure_channels_cdp, probe as probe_channels_cdp
from src.providers.channels_comments import pick_comment, work_digest_records, works_from_post_list

logger = logging.getLogger("ChannelsCommentGuard")
DATA_DIR = PROJECT_ROOT / "data" / "browser_profiles" / "channels-tech"
STATE_PATH = DATA_DIR / "storage_state.json"
CACHE_FILE = PROJECT_ROOT / "data" / "seen_channels_comments.json"
#: 作品级未读提醒的去重状态（object_id → 上次看到的评论数），与上面的评论 id 缓存分开
DIGEST_FILE = PROJECT_ROOT / "data" / "seen_channels_comment_digest.json"

#: 常驻 Chrome 的 CDP 地址（视频号固定 9334，抖音 9333，Agent-Reach 9222）
CDP_URL = os.getenv("CHANNELS_CDP_URL") or "http://127.0.0.1:9334"
#: 常驻实例上最多保留的 tab 数（超过就关最旧的，绝不关进程）
MAX_TABS = int(os.getenv("CHANNELS_MAX_TABS") or 5)


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


def _load_digest_seen() -> dict:
    try:
        return json.loads(DIGEST_FILE.read_text(encoding="utf-8")) if DIGEST_FILE.exists() else {}
    except Exception:
        return {}


def _save_digest_seen(seen: dict) -> None:
    DIGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


async def _trim_tabs(context, keep: int = MAX_TABS) -> list[str]:
    """只关多余 tab，不动进程（常驻方案铁律）。

    保留最近 `keep` 个页面，从最旧的开始关。
    """
    pages = list(context.pages)
    closed = []
    while len(pages) > keep:
        victim = pages.pop(0)
        try:
            url = victim.url
            await victim.close()
            closed.append(url)
        except Exception as e:  # noqa: BLE001 — 关闭失败不影响主流程
            logger.debug(f"关闭多余 tab 失败（忽略）：{e}")
    return closed


async def fetch_channels_unreplied_comments() -> List[Dict[str, Any]]:
    """复用常驻 Chrome（CDP 9334）抓取视频号未回复评论。

    只 `connect_over_cdp` 接管常驻实例：开一个 tab → 读完 → `page.close()`。
    不启动新浏览器、不关闭常驻浏览器进程。
    """
    from playwright.async_api import async_playwright

    comments = []
    pending: List[Dict[str, Any]] = []      # 作品级未读提醒的数据来源（post_list）
    seen_ids = load_seen_comment_ids()
    page = None
    browser = None
    preexisting: list = []

    try:
        if not probe_channels_cdp():
            logger.warning(f"常驻 Chrome（{CDP_URL}）不在，按常驻方案拉起（有头，非无头）")
            try:
                ensure_channels_cdp(headless=False)
            except Exception as e:  # noqa: BLE001
                logger.error(f"常驻 Chrome 拉起失败：{e}")
                return []

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            try:
                trimmed = await _trim_tabs(context)
                if trimmed:
                    logger.info(f"关闭多余 tab {len(trimmed)} 个（只关 tab 不关进程）：{[u[:60] for u in trimmed]}")

                # 记录进场时已有的 tab，收尾时只关「本次新开的」（含站点自己弹的 popup）
                preexisting = list(context.pages)

                page = await context.new_page()
                url = "https://channels.weixin.qq.com/platform/interaction/comment"
                logger.info(f"Navigating to WeChat Channels comment page: {url}")
                await page.goto(url, wait_until="commit", timeout=25000)
                await asyncio.sleep(6)

                # 校验登录态是否仍然有效
                if "login" in page.url:
                    logger.warning("WeChat Channels session expired, redirected to login page.")
                    return []

                # ② 严格结构化解析（2026-11-22 止血）：
                #    只认「节点里同时存在昵称元素 + 评论正文元素」的结构，
                #    并在 Python 侧再过一遍 pick_comment（时间戳/未读数/页头文案一律拒）。
                #    拿不到就**不猜**——旧实现用 `tr`/`[class*='comment']` 兜底扫，
                #    把作品 feed 卡片当成评论，导致卡片里「粉丝=评论管理 / 评论=视频」。
                candidates = await page.evaluate(
                    """() => {
                        const out = [];
                        const boxes = document.querySelectorAll(
                            "[class*='comment-item'], [class*='commentItem'], [class*='comment-detail']");
                        for (const box of boxes) {
                            const nick = box.querySelector(
                                "[class*='nickname'], [class*='user-name'], [class*='username']");
                            const body = box.querySelector(
                                "[class*='comment-content'], [class*='content-text'], [class*='reply-content']");
                            if (!nick || !body) continue;   // 结构不完整 → 不是评论
                            const raw = (box.innerText || '').trim();
                            out.push({
                                user: (nick.innerText || '').trim(),
                                content: (body.innerText || '').trim(),
                                raw,
                            });
                            if (out.length >= 20) break;
                        }
                        return out;
                    }""")
                logger.info(f"严格结构化候选 {len(candidates or [])} 个（旧实现会扫出几十个版式节点）")
                for cand in (candidates or []):
                    hit = pick_comment(cand)
                    if not hit:
                        continue
                    cid = f"chn_{hash(hit['raw']) & 0xffffffff}"
                    if cid in seen_ids:
                        continue
                    comments.append({"comment_id": cid, "user": hit["user"],
                                     "content": hit["content"], "title": "视频号",
                                     "raw_text": hit["raw"]})

                # ③ 评论正文拿不到时，退到「作品级未读提醒」：用 post_list 的标题 + 评论数，
                #    只报「哪条作品有评论待看」，**不生成粉丝/评论/建议回复**。
                if not comments:
                    try:
                        from src.providers.channels_edit import fetch_post_list
                        data = await fetch_post_list(page, 10, 1)
                        works = works_from_post_list(data)
                        pending.extend(works)
                        logger.info(f"post_list 作品 {len(works)} 条，其中有评论的 "
                                    f"{sum(1 for w in works if w['comment_count'] > 0)} 条")
                    except Exception as e:  # noqa: BLE001 — 摘要失败也不编造
                        logger.warning(f"post_list 摘要失败（不生成任何评论内容）：{type(e).__name__}: {e}")
            finally:
                # ⚠ 收尾必须留在 `async with` 内：一旦退出该块，playwright driver 已关闭，
                #   page.close() 会静默失败 → 每轮泄漏 1 个 tab（2026-11-22 实测踩到）
                closers = [page] if page is not None else []
                try:
                    for pg in context.pages:
                        if all(pg is not x for x in preexisting) and all(pg is not c for c in closers):
                            closers.append(pg)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"枚举残留 tab 失败（忽略）：{e}")
                for pg in closers:
                    try:
                        await pg.close()
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"关闭 tab 失败（忽略）：{e}")
                try:
                    await browser.close()  # connect_over_cdp 下仅断开连接，不结束常驻进程
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"断开 CDP 连接失败（忽略）：{e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error fetching WeChat Channels comments: {e}")

    fetch_channels_unreplied_comments.pending_works = pending  # type: ignore[attr-defined]
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
        # 方案 A：评论正文拿不到时，只报「哪条作品有评论待看」，绝不编粉丝/评论/建议回复
        works = getattr(fetch_channels_unreplied_comments, "pending_works", []) or []
        seen = _load_digest_seen()
        digests = work_digest_records(works, seen)
        if digests:
            for d in digests:
                seen[d["object_id"]] = d["comment_count"]
            _save_digest_seen(seen)
            logger.info(f"作品级未读提醒 {len(digests)} 条（未生成任何评论内容）")
        else:
            logger.info("无新评论、也无新的作品级未读，静默")
        return digests

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
