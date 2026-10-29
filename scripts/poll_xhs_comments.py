import os
import sys
import time
import json
import sqlite3
import logging
import yaml
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import PipelineDB
from src.safety.comment_funnel import CommentSafetyFunnel
from src.providers.xiaohongshu_cdp import XiaohongshuCDPProvider

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("XhsCommentWorker")

LOG_FILE = PROJECT_ROOT / "data" / "comment_replies_log.jsonl"

def append_audit_log(record: dict):
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def check_and_seed_empty_comments(provider: XiaohongshuCDPProvider, db: PipelineDB, funnel: CommentSafetyFunnel, dry_run: bool = False):
    """
    零评论视频冷启动巡检（基于 Brave CDP 真实环境）：
    检查近期发布的视频笔记，若评论区为空且未曾发送过 seed_comment，
    则自动生成一条关于技术话题的开放探讨向主评论（每视频终身仅限一条）。
    """
    logger.info("Checking recently published notes for cold-start seed comment via CDP...")
    seeded_records = []
    try:
        notes = provider.get_recent_notes(limit=5)
        if not notes:
            logger.info("No recent notes found on profile via CDP.")
            return []

        with db.get_conn() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
            SELECT task_id, fact_summary_150, aligned_render_json, has_seed_comment, published_video_id
            FROM content_tasks
            WHERE status = 'DRAFTED'
            ORDER BY created_at DESC
            LIMIT 15
            """)
            tasks = [dict(r) for r in cur.fetchall()]

        for note in notes[:5]:
            feed_id = note.get("feed_id")
            display_title = (note.get("title") or "").strip()

            # 匹配本地数据库任务
            matched_task = None
            for t in tasks:
                if feed_id and t.get("published_video_id") == feed_id:
                    matched_task = t
                    break
                if not matched_task and display_title:
                    try:
                        al = json.loads(t.get("aligned_render_json") or "{}")
                        t_title = al.get("title", "")
                        if t_title and (display_title[:6] in t_title or t_title[:6] in display_title):
                            matched_task = t
                            break
                    except Exception:
                        pass

            if matched_task and matched_task.get("has_seed_comment"):
                logger.info(f"Seed comment already posted for task {matched_task.get('task_id')}, skipping.")
                continue

            # 通过 CDP 弹窗精准读取评论区状态
            detail = provider.get_note_detail_and_comments(feed_id)
            if not detail.get("success"):
                continue

            comments_list = detail.get("comments", [])
            if len(comments_list) > 0:
                logger.info(f"Note '{display_title}' has {len(comments_list)} comment(s), skipping seed.")
                continue

            logger.info(f"Found zero-comment video: '{display_title}'. Generating seed comment...")
            fact_summary = (matched_task.get("fact_summary_150") if matched_task else "") or display_title or "前沿开源项目与AI技术快讯"
            seed_comment = funnel.generate_seed_comment(fact_summary)
            if not seed_comment:
                continue

            passed, _, _ = funnel.level1_check(seed_comment)
            if not passed:
                logger.warning(f"Seed comment failed level1 check: {seed_comment}")
                continue

            logger.info(f"Generated seed comment: '{seed_comment}'")
            success = False
            if not dry_run:
                success = provider.post_seed_comment(feed_id=feed_id, content=seed_comment)
            else:
                logger.info(f"[DRY RUN] Would post seed comment: '{seed_comment}'")
                success = True

            if success:
                with db.get_conn() as conn:
                    cur = conn.cursor()
                    if matched_task:
                        cur.execute("""
                        UPDATE content_tasks
                        SET has_seed_comment = 1, seed_comment_text = ?, seed_comment_at = CURRENT_TIMESTAMP, published_video_id = ?
                        WHERE task_id = ?
                        """, (seed_comment, feed_id or "cdp_feed", matched_task.get("task_id")))
                    conn.commit()

                append_audit_log({
                    "timestamp": datetime.now().isoformat(),
                    "platform": "xiaohongshu",
                    "feed_id": feed_id,
                    "feed_title": display_title,
                    "comment_id": "seed_main_comment",
                    "user": "博主自留(冷启动)",
                    "raw_comment": "[SEED_TOPIC_COMMENT]",
                    "classification": "SEED",
                    "reply": seed_comment,
                    "status": "SEED_POSTED" if not dry_run else "DRY_RUN"
                })
                if not dry_run:
                    seeded_records.append({
                        "platform": "小红书",
                        "feed_title": display_title,
                        "user": "博主自留(冷启动)",
                        "comment": "（冷启动抛砖引玉）",
                        "reply": seed_comment
                    })
                break
        return seeded_records
    except Exception as e:
        logger.error(f"Error checking and seeding comments via CDP: {e}")
        return []

def poll_and_reply_xhs_comments(dry_run: bool = False):
    logger.info("Starting live Xiaohongshu comment poll and guard cycle via CDP...")
    provider = XiaohongshuCDPProvider()
    if not provider.check_login_status():
        logger.warning("Xiaohongshu is NOT logged in inside Brave (port 9222).")
        return []

    db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
    funnel = CommentSafetyFunnel()

    hook = "开源项目直接在 GitHub 搜索对应项目名即可查看源码，评论区AI已接入，欢迎交流～"
    accounts_file = PROJECT_ROOT / "config" / "accounts.yaml"
    if accounts_file.exists():
        try:
            with open(accounts_file, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            xhs_acc = next((a for a in cfg.get("accounts", []) if a.get("platform") == "xiaohongshu"), {})
            hook = xhs_acc.get("private_domain_hook", hook)
        except Exception as e:
            logger.warning(f"Failed to load accounts.yaml: {e}")

    notifications = provider.get_mentions_and_comments(limit=20)
    logger.info(f"Fetched {len(notifications)} notification(s) from Xiaohongshu via CDP.")

    processed_count = 0
    replied_records = []

    for item in notifications:
        comment_id = item.get("comment_id")
        comment_text = (item.get("comment_text") or "").strip()
        user_name = item.get("user_name", "未知用户")
        feed_id = item.get("feed_id", "")
        feed_title = item.get("feed_title", "")

        if not comment_id or not comment_text:
            continue

        # 查重检查：仅当已成功回复（status = 'REPLIED'）时才跳过，避免偶发超时失败后被永久锁死
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT status FROM comment_interactions
            WHERE (comment_id = ? OR raw_comment = ?) AND status = 'REPLIED'
            """, (comment_id, comment_text))
            if cur.fetchone():
                logger.info(f"Comment already replied successfully in DB: [{comment_id}] '{comment_text}' by {user_name}")
                continue

        logger.info(f"Processing new Xiaohongshu comment: [{comment_id}] '{comment_text}' by {user_name} on '{feed_title}'")

        fact_summary = feed_title or "前沿开源项目与AI技术快讯，源码与技术复现指南。"
        keyword = "AI"

        res = funnel.process_comment(
            raw_comment=comment_text,
            fact_summary_150=fact_summary,
            resource_keyword=keyword,
            private_domain_hook=hook
        )
        classification = res.get("classification")
        reply_content = res.get("reply")
        logger.info(f"Funnel verdict for XHS: Classification={classification}, Reply='{reply_content}'")

        reply_success = False
        if reply_content and not dry_run:
            try:
                reply_success = provider.reply_comment(
                    comment_id=comment_id,
                    content=reply_content,
                    user_name=user_name,
                    comment_text=comment_text
                )
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
                    logger.warning(f"❌ Failed to reply to {user_name} on Xiaohongshu via CDP.")
            except Exception as e:
                logger.error(f"Error executing reply on Xiaohongshu: {e}")
        elif reply_content and dry_run:
            logger.info(f"[DRY RUN] Would reply to {user_name} on Xiaohongshu: '{reply_content}'")
            replied_records.append({
                "platform": "小红书(DRY_RUN)",
                "feed_title": feed_title,
                "user": user_name,
                "comment": comment_text,
                "reply": reply_content
            })

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

    # 第二阶段：无评论视频冷启动检查（仅在未读通知处理完毕后执行，且每轮至多触发 1 条）
    seeded_records = check_and_seed_empty_comments(provider, db, funnel, dry_run=dry_run)
    if seeded_records:
        replied_records.extend(seeded_records)

    return replied_records

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Xiaohongshu Comment Worker via CDP")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode without posting")
    args = parser.parse_args()

    poll_and_reply_xhs_comments(dry_run=args.dry_run)

if __name__ == "__main__":
    main()
