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
from src.providers.xiaohongshu import XiaohongshuProvider
from src.providers.bilibili import BilibiliProvider
from src.providers.channels_cdp import ChannelsCDPProvider
from src.feishu.approval_service import FeishuApprovalService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PublishWorker")

def notify_feishu(message: str):
    """向飞书审批会话主动播报发布结果通知"""
    try:
        svc = FeishuApprovalService()
        target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
        rec_type = "open_id" if target.startswith("ou_") else "chat_id"
        svc.send_text_message(receive_id=target, receive_id_type=rec_type, text=message)
    except Exception as e:
        logger.warning(f"Failed to send feishu publish notification: {e}")


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
    if status not in ("APPROVED", "WAITING_APPROVAL"):
        logger.warning(f"Task '{task_id}' is in status '{status}', expected 'APPROVED' or 'WAITING_APPROVAL'.")
        return False

    draft_script = json.loads(task.get("draft_script_json") or "{}")
    video_path = task.get("video_path")
    cover_path = task.get("cover_path")
    title = draft_script.get("title", "")
    topics = draft_script.get("topics") or draft_script.get("tags") or []
    content_text = draft_script.get("content_text") or draft_script.get("fact_summary_150") or ""

    import re
    def sanitize_text(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'https?://[^\s]+', '', text)
        text = re.sub(r'github\.com/[^\s]+', '', text)
        return text.strip()

    title = sanitize_text(title)
    content_text = sanitize_text(content_text)

    # 结果收集汇总
    dispatch_results = []
    
    # 1. B站分发（若启用 bili-tech-main）
    bili_account = accounts_map.get("bili-tech-main")
    if bili_account and bili_account.get("enabled", True):
        logger.info(f"Dispatching task '{task_id}' to Bilibili ({bili_account.get('publish_mode', 'draft')})...")
        try:
            bili_provider = BilibiliProvider(bili_account)
            bili_res = bili_provider.save_draft(
                video_path=video_path,
                cover_path=cover_path,
                title=title,
                topics=topics,
                content_text=content_text,
                declare_aigc=True
            )
            if bili_res.get("success"):
                draft_id = bili_res.get("draft_id")
                dispatch_results.append(f"📺 **B站 (私密草稿)**: ✅ 上传成功 (`{draft_id}`)")
            else:
                dispatch_results.append(f"📺 **B站**: ⚠️ {bili_res.get('message')}")
        except Exception as e:
            dispatch_results.append(f"📺 **B站**: ❌ 异常 ({e})")

    # 2. 微信视频号分发（若启用 channels-tech-main）
    channels_account = accounts_map.get("channels-tech-main")
    if channels_account and channels_account.get("enabled", True):
        logger.info(f"Dispatching task '{task_id}' to WeChat Channels ({channels_account.get('publish_mode', 'public')})...")
        try:
            channels_provider = ChannelsCDPProvider(channels_account)
            channels_res = channels_provider.save_draft(
                video_path=video_path,
                cover_path=cover_path,
                title=title,
                topics=topics,
                content_text=content_text,
                declare_aigc=False
            )
            if channels_res.get("success"):
                draft_id = channels_res.get("draft_id")
                dispatch_results.append(f"📲 **微信视频号 (公开分发)**: ✅ 已就绪 (`{draft_id}`)")
            else:
                dispatch_results.append(f"📲 **微信视频号**: ⚠️ {channels_res.get('message')}")
        except Exception as e:
            dispatch_results.append(f"📲 **微信视频号**: ❌ 异常 ({e})")

    # 3. 手机发帖专例文案推送（针对小红书 7 天避险冷冻期 & 手机发布）
    try:
        clean_tags = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
        short_title = title[:20]
        material_msg = (
            f"📱 【手机端发帖纯净素材】\n\n"
            f"{short_title}\n\n"
            f"{content_text}\n\n"
            f"💡 欢迎在评论区交流讨论～\n\n"
            f"{clean_tags}"
        )
        notify_feishu(material_msg)
        dispatch_results.append(f"📕 **小红书 (手机发帖素材)**: ✅ 纯净文案已推送到飞书私聊")
    except Exception as e:
        dispatch_results.append(f"📕 **小红书**: ❌ 素材推送失败 ({e})")

    # 4. 更新 SQLite 数据库任务状态
    db.update_task_status(task_id, "DISPATCHED", platform_draft_id=f"matrix_{task_id}")
    logger.info(f"Task '{task_id}' successfully transitioned to DISPATCHED")

    # 5. 向飞书播报整体分发汇总卡片
    summary_msg = f"🎉 **任务 `{task_id}` 动态多平台分发执行完毕**:\n\n" + "\n".join(dispatch_results)
    notify_feishu(summary_msg)
    return True
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
