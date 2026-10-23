#!/usr/bin/env python3
"""
scripts/run_publish_worker.py
Phase 3: 草稿箱上传与发布 Worker
轮询或手动接力 SQLite 中状态为 APPROVED 的任务，
调度对应的 Provider (Douyin / Xiaohongshu) 自动化上传视频、填写文案与话题标签，
并保存至平台草稿箱（严格遵循草稿箱模式，绝不直发）。
上传完成后将状态更新为 DRAFTED，记录 platform_draft_id。
"""

import os
import sys
import json
import logging
import argparse
import yaml
from pathlib import Path
from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

load_dotenv(PIPELINE_ROOT / ".env")

from src.db import PipelineDB
from src.providers.douyin import DouyinProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PublishWorker")


def load_accounts_map():
    config_path = PIPELINE_ROOT / "config" / "accounts.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {acc["id"]: acc for acc in data.get("accounts", [])}


def process_task(task_id: str, db: PipelineDB, accounts_map: dict):
    task = db.get_task(task_id)
    if not task:
        logger.error(f"Task '{task_id}' not found in SQLite DB.")
        return False

    status = task.get("status")
    if status != "APPROVED":
        logger.warning(f"Task '{task_id}' is in status '{status}', expected 'APPROVED'.")
        return False

    target_account_id = task.get("target_account_id", "douyin-tech-main")
    account_config = accounts_map.get(target_account_id)
    if not account_config:
        logger.error(f"Account configuration for '{target_account_id}' not found.")
        return False

    draft_script = json.loads(task.get("draft_script_json") or "{}")
    video_path = task.get("video_path")
    cover_path = task.get("cover_path")
    title = draft_script.get("title", "")
    topics = draft_script.get("topics") or draft_script.get("tags") or []
    content_text = draft_script.get("content_text") or draft_script.get("fact_summary_150") or ""

    if not video_path or not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return False

    platform = account_config.get("platform", "douyin")
    logger.info(f"Dispatching task '{task_id}' to provider: {platform} (Account: {target_account_id})")

    if platform == "douyin":
        provider = DouyinProvider(account_config)
        res = provider.save_draft(
            video_path=video_path,
            cover_path=cover_path,
            title=title,
            topics=topics,
            content_text=content_text
        )

        if res.get("success"):
            draft_id = res.get("draft_id", f"draft_{task_id}")
            db.update_task_status(task_id, "DRAFTED", platform_draft_id=draft_id)
            logger.info(f"✅ Task '{task_id}' successfully saved as draft! draft_id={draft_id}")
            return True
        else:
            logger.error(f"❌ Failed to save draft for task '{task_id}': {res.get('message') or res.get('error')}")
            return False
    else:
        logger.error(f"Unsupported platform: {platform}")
        return False


def main():
    parser = argparse.ArgumentParser(description="草稿箱上传与发布 Worker")
    parser.add_argument("--task-id", help="指定要处理的 APPROVED 任务 ID")
    parser.add_argument("--poll", action="store_true", help="以轮询模式持续监听 APPROVED 任务")
    args = parser.parse_args()

    db_path = os.getenv("SQLITE_DB_PATH", str(PIPELINE_ROOT / "data/pipeline_state.db"))
    db = PipelineDB(db_path)
    accounts_map = load_accounts_map()

    if args.task_id:
        process_task(args.task_id, db, accounts_map)
    else:
        logger.info("Checking for any APPROVED tasks in DB...")
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT task_id FROM content_tasks WHERE status = 'APPROVED' ORDER BY updated_at ASC")
            rows = cur.fetchall()

        if not rows:
            logger.info("No tasks currently in APPROVED status.")
        else:
            logger.info(f"Found {len(rows)} APPROVED task(s). Processing...")
            for row in rows:
                process_task(row[0], db, accounts_map)


if __name__ == "__main__":
    main()
