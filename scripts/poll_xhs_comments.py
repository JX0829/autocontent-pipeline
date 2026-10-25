import os
import sys
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import PipelineDB
from src.safety.comment_funnel import CommentSafetyFunnel
from src.providers.xiaohongshu import XiaohongshuProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("XhsCommentWorker")

LOG_FILE = PROJECT_ROOT / "data" / "comment_replies_log.jsonl"

def append_audit_log(record: dict):
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def poll_and_reply_xhs_comments(dry_run: bool = False):
    logger.info("Starting live Xiaohongshu comment poll and guard cycle...")
    db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
    funnel = CommentSafetyFunnel()

    with open(PROJECT_ROOT / "config" / "accounts.yaml") as f:
        cfg = yaml.safe_load(f)
    
    xhs_acc = next((a for a in cfg.get("accounts", []) if a.get("platform") == "xiaohongshu"), None)
    if not xhs_acc or not xhs_acc.get("enabled", True):
        logger.warning("Xiaohongshu account not configured or disabled.")
        return

    provider = XiaohongshuProvider(xhs_acc)
    if not provider.check_login_status():
        logger.error("Xiaohongshu account is not logged in!")
        return

    notifications = provider.get_mentions_and_comments(limit=20)
    logger.info(f"Fetched {len(notifications)} notification(s) from Xiaohongshu.")

    processed_count = 0
    replied_records = []
    for item in notifications:
        comment_id = item.get("comment_id")
        comment_text = (item.get("comment_text") or "").strip()
        user_info = item.get("from", {})
        user_name = user_info.get("nickname", "未知用户")
        feed_id = item.get("feed_id", "")
        feed_title = item.get("feed_title", "")

        if not comment_id or not comment_text:
            continue

        # 查重检查（根据 comment_id 或 文本）
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM comment_interactions WHERE comment_id = ? OR raw_comment = ?", (comment_id, comment_text))
            if cur.fetchone():
                logger.info(f"Comment already processed in DB: [{comment_id}] '{comment_text}' by {user_name}")
                continue

        logger.info(f"Processing new Xiaohongshu comment: [{comment_id}] '{comment_text}' by {user_name} on '{feed_title}'")

        # 动态事实上下文，可根据 feed_title 调整
        fact_summary = feed_title or "前沿开源项目与AI技术快讯，源码与技术复现指南。"
        keyword = "AI"
        hook = xhs_acc.get("private_domain_hook", "开源项目直接在 GitHub 搜索对应项目名即可查看源码，欢迎在评论区一起交流探讨～")

        res = funnel.process_comment(comment_text, fact_summary, keyword, hook)
        classification = res.get("classification")
        reply_content = res.get("reply")
        logger.info(f"Funnel verdict for XHS: Classification={classification}, Reply='{reply_content}'")

        reply_success = False
        if reply_content and not dry_run:
            try:
                reply_success = provider.reply_comment(comment_id=comment_id, content=reply_content)
                if reply_success:
                    logger.info(f"✅ Successfully replied to {user_name} on Xiaohongshu: '{reply_content}'")
                    replied_records.append({
                        "platform": "小红书",
                        "feed_title": feed_title,
                        "user": user_name,
                        "comment": comment_text,
                        "reply": reply_content
                    })
                else:
                    logger.warning(f"❌ Failed to reply to {user_name} on Xiaohongshu via MCP.")
            except Exception as e:
                logger.error(f"Error executing reply on Xiaohongshu: {e}")

        # 状态持久化入库
        status = "REPLIED" if reply_success else ("DRY_RUN" if dry_run and reply_content else ("IGNORED" if not reply_content else "FAILED"))
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO comment_interactions
            (comment_id, task_id, published_video_id, raw_comment, sanitized_comment, classification, reply_content, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                comment_id,
                f"xhs_{feed_id}",
                feed_id or "xhs_feed",
                comment_text,
                res.get("sanitized_comment", comment_text),
                classification,
                reply_content,
                status
            ))
            conn.commit()

        append_audit_log({
            "timestamp": datetime.now().isoformat(),
            "platform": "xiaohongshu",
            "feed_id": feed_id,
            "feed_title": feed_title,
            "comment_id": comment_id,
            "user": user_name,
            "raw_comment": comment_text,
            "classification": classification,
            "reply": reply_content,
            "status": status
        })
        processed_count += 1
        time.sleep(2)

    logger.info(f"Xiaohongshu comment cycle finished. Handled {processed_count} comment(s).")
    return replied_records

if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    poll_and_reply_xhs_comments(dry_run=is_dry)
