import os
import sys
import time
import json
import sqlite3
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

def check_and_seed_empty_comments(provider: XiaohongshuProvider, db: PipelineDB, funnel: CommentSafetyFunnel, dry_run: bool = False):
    """
    零评论视频冷启动巡检：
    检查近期发布的视频笔记，若评论区为空且未曾发送过 seed_comment，
    则自动生成一条关于技术话题的开放探讨向主评论（抛砖引玉，每视频终身仅限一条）。
    """
    logger.info("Checking recently published notes for cold-start seed comment...")
    try:
        profile_res = provider.call_tool("get_my_profile", {"tab": "note"})
        if not profile_res.get("success"):
            logger.warning("Failed to fetch my profile notes for seed check.")
            return

        data = json.loads(profile_res.get("raw_text", "{}"))
        feeds = data.get("feeds", [])
        if not feeds:
            logger.info("No feeds found on my profile.")
            return

        # 获取数据库中已记录的 tasks
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

        # 遍历最近 5 篇笔记（避免扫描过多造成限频）
        for feed in feeds[:5]:
            feed_id = feed.get("id")
            xsec_token = feed.get("xsecToken")
            note_card = feed.get("noteCard", {})
            display_title = (note_card.get("displayTitle") or "").strip()
            interact = note_card.get("interactInfo", {})
            comment_count_str = interact.get("commentCount", "")

            if not feed_id or not xsec_token:
                continue

            # 判断是否有用户评论（小红书前端空评论时 commentCount 为 '' 或 '0'）
            has_comments = False
            if comment_count_str and comment_count_str not in ["", "0"]:
                has_comments = True

            # 匹配本地数据库任务（通过 feed_id 匹配，或通过 displayTitle 模糊匹配）
            matched_task = None
            for t in tasks:
                if t.get("published_video_id") == feed_id:
                    matched_task = t
                    break
                if not matched_task and display_title:
                    try:
                        al = json.loads(t.get("aligned_render_json") or "{}")
                        t_title = al.get("title", "")
                        # 标题前 6 个字符匹配
                        if t_title and display_title[:6] in t_title:
                            matched_task = t
                            break
                    except Exception:
                        pass

            # 查重检查：如果 DB 记录该任务已发过 seed comment，跳过
            if matched_task and matched_task.get("has_seed_comment"):
                logger.info(f"Seed comment already posted for task {matched_task.get('task_id')} ({feed_id}), skipping.")
                continue

            # 针对无评论笔记执行进一步精准复核：拉取详情确认 comment 确实为 0
            if not has_comments:
                detail_res = provider.call_tool("get_feed_detail", {
                    "feed_id": feed_id,
                    "xsec_token": xsec_token,
                    "load_all_comments": False
                })
                detail_data = json.loads(detail_res.get("raw_text", "{}"))
                comments_list = detail_data.get("comments") or []
                if len(comments_list) > 0:
                    logger.info(f"Feed {feed_id} actually has {len(comments_list)} comment(s), skipping seed.")
                    continue

                # 确认评论区为空，准备生成 1 条探讨话题主评论
                logger.info(f"Found zero-comment video: [{feed_id}] '{display_title}'. Generating seed comment...")
                fact_summary = (matched_task.get("fact_summary_150") if matched_task else "") or display_title or "前沿开源项目与AI技术快讯"
                seed_comment = funnel.generate_seed_comment(fact_summary)
                if not seed_comment:
                    logger.warning(f"Failed to generate seed comment for feed {feed_id}")
                    continue

                logger.info(f"Generated seed comment for [{feed_id}]: '{seed_comment}'")

                success = False
                if not dry_run:
                    success = provider.post_seed_comment(feed_id=feed_id, xsec_token=xsec_token, content=seed_comment)
                    logger.info(f"Post seed comment result for {feed_id}: {success}")
                else:
                    logger.info(f"[DRY RUN] Would post seed comment to {feed_id}: '{seed_comment}'")
                    success = True

                if success:
                    # 更新数据库状态
                    with db.get_conn() as conn:
                        cur = conn.cursor()
                        if matched_task:
                            cur.execute("""
                            UPDATE content_tasks
                            SET has_seed_comment = 1, seed_comment_text = ?, seed_comment_at = CURRENT_TIMESTAMP, published_video_id = ?
                            WHERE task_id = ?
                            """, (seed_comment, feed_id, matched_task.get("task_id")))
                        else:
                            # 插入一条虚拟 task 记录防止重复触发
                            cur.execute("""
                            INSERT OR REPLACE INTO content_tasks
                            (task_id, source_url, entity_key, theme, target_account_id, status, has_seed_comment, seed_comment_text, seed_comment_at, published_video_id)
                            VALUES (?, 'xhs_feed', ?, 'tech', 'xhs-tech-main', 'DRAFTED', 1, ?, CURRENT_TIMESTAMP, ?)
                            """, (f"xhs_{feed_id}", f"xhs_feed_{feed_id}", seed_comment, feed_id))
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
                    # 每次轮询至多发布 1 条冷启动评论，避免突发连续发多条触发风控
                    break

    except Exception as e:
        logger.error(f"Error checking and seeding comments: {e}")

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

    # 第二阶段：无评论视频冷启动检查（仅在未读通知处理完毕后执行，且每轮至多触发 1 条）
    check_and_seed_empty_comments(provider, db, funnel, dry_run=dry_run)

    return replied_records

if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    poll_and_reply_xhs_comments(dry_run=is_dry)
