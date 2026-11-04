#!/usr/bin/env python3
"""
scripts/poll_bilibili_comments.py
Bilibili 评论自动巡检、安全过滤与智能回复脚本
支持：
1. 真实读取创作者收到的未读回帖/评论
2. 接入 CommentSafetyFunnel 三级安全合规漏斗与回复生成
3. 48 小时状态防重排重
4. 支持 live 自动直发与 dry_run 生成建议话术
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.bilibili_comment import BilibiliCommentClient
from src.safety.comment_funnel import CommentSafetyFunnel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("BilibiliCommentWorker")

DATA_DIR = PROJECT_ROOT / "data"
SEEN_FILE = DATA_DIR / "seen_bilibili_comments.json"
AUDIT_LOG_FILE = DATA_DIR / "comment_replies_log.jsonl"

def load_seen_comments() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_seen_comments(seen_dict: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now_ts = time.time()
    # 淘汰 30 天以上的历史条目
    cleaned = {k: v for k, v in seen_dict.items() if (now_ts - v.get("timestamp", 0)) < (86400 * 30)}
    SEEN_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")

def append_audit_log(record: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def poll_and_reply_bilibili_comments(dry_run: bool = False) -> list:
    """
    轮询 B 站最新互动评论并完成智能安全回复
    :param dry_run: 若为 True，仅生成 AI 建议回复而不实际调用 API 发送
    :return: 处理的记录列表（用于飞书通知）
    """
    logger.info(f"=== Starting Bilibili Comment Polling (dry_run={dry_run}) ===")
    client = BilibiliCommentClient()
    if not client.is_available():
        logger.warning("Bilibili client is not available (check cookies). Skipping.")
        return []

    funnel = CommentSafetyFunnel()
    seen_dict = load_seen_comments()

    raw_comments = client.fetch_latest_replies()
    logger.info(f"Retrieved {len(raw_comments)} latest replies from Bilibili.")

    replied_records = []

    for item in raw_comments:
        rpid_key = str(item.get("rpid"))
        comment_text = item.get("comment", "")
        user_name = item.get("user", "")
        feed_title = item.get("feed_title", "")
        oid = item.get("oid")
        rpid = item.get("rpid")
        root_id = item.get("root_id", 0)

        # 1. 防重判断
        if rpid_key in seen_dict:
            continue

        logger.info(f"Processing new Bilibili comment: [{rpid_key}] '{comment_text}' by {user_name} on '{feed_title}'")

        # 2. 调用安全漏斗审查与回复生成
        fact_summary = f"{feed_title}。源码与极客技术探讨，欢迎在评论区交流。"
        context_dict = {
            "title": feed_title,
            "fact_summary": fact_summary,
            "resource_keyword": "1024",
            "video_context": fact_summary,
            "platform": "bilibili"
        }

        try:
            funnel_res = funnel.process_comment(
                raw_comment=comment_text,
                fact_summary_150=fact_summary,
                resource_keyword="1024",
                private_domain_hook="欢迎在评论区交流讨论"
            )
        except Exception as e:
            logger.error(f"Error during funnel evaluation: {e}")
            funnel_res = {"action": "DROP", "reply": "", "reason": f"Funnel exception: {e}"}

        action = funnel_res.get("action")
        reply_content = (funnel_res.get("reply") or "").strip()

        # 3. 执行回复或转人工建议
        final_reply = reply_content
        send_success = False

        if action in ["AUTO_REPLY", "CONVERT_RESOURCE"] and final_reply and oid and rpid:
            if not dry_run:
                resp = client.reply_comment(
                    oid=int(oid),
                    rpid=int(rpid),
                    message=final_reply,
                    root_id=int(root_id) if root_id else 0
                )
                send_success = resp.get("success", False)
                logger.info(f"Live reply result for rpid {rpid}: success={send_success}")
            else:
                send_success = True
                logger.info(f"[Dry-Run] Generated suggested reply: '{final_reply}'")

        # 4. 登记已处理状态
        seen_dict[rpid_key] = {
            "user": user_name,
            "comment": comment_text,
            "reply": final_reply,
            "action": action,
            "timestamp": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        record = {
            "platform": "bilibili",
            "feed_title": feed_title,
            "user": user_name,
            "comment": comment_text,
            "reply": final_reply or "（该评论已按风控策略静默过滤）",
            "action": action,
            "status": "SENT" if (send_success and not dry_run) else ("SUGGESTED" if dry_run else "DROPPED"),
            "reply_mode": "dry_run" if dry_run else "auto",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        if final_reply:
            replied_records.append(record)
            append_audit_log(record)

    # 保存防重记录
    save_seen_comments(seen_dict)
    logger.info(f"Bilibili poll cycle finished. Handled {len(replied_records)} interactive replies.")
    return replied_records

if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv
    records = poll_and_reply_bilibili_comments(dry_run=is_dry)
    print(f"Processed records: {len(records)}")
