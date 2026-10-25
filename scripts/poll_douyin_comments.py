import os
import sys
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import PipelineDB
from src.safety.comment_funnel import CommentSafetyFunnel
from src.providers.douyin import DouyinProvider
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DouyinCommentWorker")

LOG_FILE = PROJECT_ROOT / "data" / "comment_replies_log.jsonl"

def append_audit_log(record: dict):
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def poll_and_reply_live_comments():
    logger.info("Starting live Douyin comment poll and guard cycle across all videos...")
    db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
    funnel = CommentSafetyFunnel()

    with open(PROJECT_ROOT / "config" / "accounts.yaml") as f:
        cfg = yaml.safe_load(f)
    acc = [a for a in cfg["accounts"] if a["id"] == "douyin-tech-main"][0]
    provider = DouyinProvider(acc)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context, browser = provider._get_browser_context(p, headless=True)
        page = context.new_page()

        try:
            page.goto("https://creator.douyin.com/creator-micro/interactive/comment", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(4000)

            refresh_btn = page.locator("text=点击刷新").first
            if refresh_btn.count() > 0:
                refresh_btn.click()
                page.wait_for_timeout(2000)
        except Exception as e:
            logger.error(f"Navigation error: {e}")
            browser.close()
            return

        # 1. 打开“选择作品”弹窗，扫描全量视频列表
        select_btn = page.locator("button:has-text('选择作品')").first
        if not select_btn or select_btn.count() == 0:
            select_btn = page.locator("span:has-text('选择作品')").first
        
        if not select_btn or select_btn.count() == 0:
            logger.warning("Could not find '选择作品' button on Douyin interactive page.")
            browser.close()
            return

        select_btn.scroll_into_view_if_needed()
        select_btn.click(force=True)
        page.wait_for_timeout(3000)

        dialog = page.locator("div[role='dialog']").first
        if dialog.count() == 0 or not dialog.is_visible():
            logger.warning("Modal dialog for works did not appear.")
            browser.close()
            return

        # 分析整段文本结构：每条视频通常由三行组成：[标题, '发布于...', '评论数']
        lines = [l.strip() for l in dialog.inner_text().split("\n") if l.strip()]
        target_indices = []
        video_idx = 0

        for i, l in enumerate(lines):
            if "发布于" in l and i + 1 < len(lines):
                cnt_str = lines[i + 1]
                title = lines[i - 1] if i > 0 else f"视频_{video_idx}"
                if cnt_str.isdigit() and int(cnt_str) > 0:
                    target_indices.append({
                        "idx": video_idx,
                        "title": title,
                        "count": int(cnt_str)
                    })
                video_idx += 1

        logger.info(f"Identified {len(target_indices)} video(s) with active comments: {[t['title'][:20] for t in target_indices]}")

        # 关闭弹窗
        close_btn = dialog.locator("button[aria-label='Close'], [class*='close']").first
        if close_btn.count() > 0:
            close_btn.click(force=True)
            page.wait_for_timeout(1000)
        else:
            page.keyboard.press("Escape")
            page.wait_for_timeout(1000)

        total_processed = 0
        replied_records = []

        # 2. 遍历有评论的视频，通过精确索引切入
        for target in target_indices:
            idx = target["idx"]
            v_title = target["title"]
            logger.info(f"--- Switching to video [{idx}]: '{v_title[:25]}' (expected {target['count']} comments) ---")

            select_btn.scroll_into_view_if_needed()
            select_btn.click(force=True)
            page.wait_for_timeout(2000)

            # 获取弹窗中的对应卡片并点击
            pub_elements = page.locator("div[role='dialog'] >> text=发布于").all()
            if idx >= len(pub_elements):
                logger.warning(f"Index {idx} out of range in dialog, skipping.")
                page.keyboard.press("Escape")
                continue

            target_pub = pub_elements[idx]
            target_card = target_pub.locator("xpath=../..")
            target_card.scroll_into_view_if_needed()
            target_card.click(force=True)
            page.wait_for_timeout(3500)

            # 抓取当前视频下的评论容器
            comment_containers = page.locator('div[class*="container-"]').all()
            extracted_comments = []

            for c_el in comment_containers:
                try:
                    txt = c_el.inner_text().strip()
                    if "回复" in txt and ("刚刚" in txt or "前" in txt or "202" in txt):
                        c_lines = [cl.strip() for cl in txt.split("\n") if cl.strip()]
                        if len(c_lines) >= 3:
                            user_name = c_lines[0]
                            comment_text = c_lines[2]
                            if comment_text not in ["回复", "删除", "举报", "置顶", "全部评论", "选择作品"]:
                                reply_btn = c_el.locator('text="回复"').first
                                extracted_comments.append({
                                    "user": user_name,
                                    "text": comment_text,
                                    "reply_btn": reply_btn,
                                    "container": c_el
                                })
                except Exception:
                    pass

            logger.info(f"Found {len(extracted_comments)} comment element(s) under '{v_title[:20]}'")

            fact_summary = v_title
            keyword = "AI"
            hook = "开源项目直接在 GitHub 搜索对应项目名即可查看源码，欢迎在评论区一起交流探讨～"

            for item in extracted_comments:
                comment_text = item["text"]
                user_name = item["user"]

                # 查重
                with db.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT 1 FROM comment_interactions WHERE raw_comment = ?", (comment_text,))
                    if cur.fetchone():
                        logger.info(f"Comment already processed in DB: '{comment_text}' by {user_name}")
                        continue

                logger.info(f"Processing new live comment: '{comment_text}' by {user_name} on '{v_title[:20]}'")
                res = funnel.process_comment(comment_text, fact_summary, keyword, hook)
                reply_content = res.get("reply")
                logger.info(f"Funnel verdict: Classification={res['classification']}, Reply='{reply_content}'")

                reply_success = False
                if reply_content:
                    try:
                        r_btn = item["reply_btn"]
                        r_btn.click()
                        page.wait_for_timeout(1000)

                        active_input = page.locator("div[contenteditable='true']").last
                        active_input.click()
                        active_input.press_sequentially(reply_content, delay=35)
                        page.wait_for_timeout(1000)

                        send_candidates = page.locator("span:has-text('发送'), button:has-text('发送')").all()
                        for b in reversed(send_candidates):
                            if b.is_visible() and b.inner_text().strip() == "发送":
                                b.click()
                                page.wait_for_timeout(2500)
                                reply_success = True
                                break
                        logger.info(f"✅ Replied to {user_name} on Douyin: '{reply_content}'")
                        replied_records.append({
                            "platform": "抖音",
                            "feed_title": v_title,
                            "user": user_name,
                            "comment": comment_text,
                            "reply": reply_content
                        })
                    except Exception as e:
                        logger.error(f"Failed to submit reply on DOM: {e}")

                # 状态持久化
                with db.get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                    INSERT OR REPLACE INTO comment_interactions
                    (comment_id, task_id, published_video_id, raw_comment, sanitized_comment, classification, reply_content, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        f"dy_{int(time.time())}_{total_processed}",
                        v_title[:30],
                        "douyin_video",
                        comment_text,
                        res["sanitized_comment"],
                        res["classification"],
                        reply_content,
                        "REPLIED" if reply_success else ("IGNORED" if not reply_content else "FAILED")
                    ))
                    conn.commit()

                append_audit_log({
                    "timestamp": datetime.now().isoformat(),
                    "platform": "douyin",
                    "video": v_title,
                    "user": user_name,
                    "raw_comment": comment_text,
                    "classification": res["classification"],
                    "reply": reply_content,
                    "status": "REPLIED" if reply_success else "SKIPPED_OR_IGNORED"
                })
                total_processed += 1
                page.wait_for_timeout(2000)

        browser.close()
        logger.info(f"Douyin comment cycle completed. Handled {total_processed} live comment(s).")
        return replied_records

if __name__ == "__main__":
    poll_and_reply_live_comments()
