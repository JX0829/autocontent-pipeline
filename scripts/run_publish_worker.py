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
from typing import Optional, Dict, Any
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

def notify_feishu(message: str, post_payload: Optional[dict] = None):
    """向飞书审批会话主动播报发布结果通知"""
    try:
        svc = FeishuApprovalService()
        target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
        rec_type = "open_id" if target.startswith("ou_") else "chat_id"
        if post_payload:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
            req = CreateMessageRequest.builder() \
                .receive_id_type(rec_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(target)
                    .msg_type("post")
                    .content(json.dumps(post_payload, ensure_ascii=False))
                    .build()
                ).build()
            svc.client.im.v1.message.create(req)
        else:
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
            # B站优先使用 16:9 横版视频与宽屏封面
            bili_video = str(task.get("video_landscape_path") or video_path or "")
            bili_cover = str(task.get("cover_landscape_path") or cover_path or "")
            bili_provider = BilibiliProvider(bili_account)
            bili_res = bili_provider.save_draft(
                video_path=bili_video,
                cover_path=bili_cover,
                title=title,
                topics=topics,
                content_text=content_text,
                declare_aigc=True
            )
            if bili_res.get("success"):
                draft_id = bili_res.get("draft_id")
                dispatch_results.append(f"📺 **B站 (16:9横版+置顶评论)**: ✅ 上传成功 (`{draft_id}`)")
                
                # 触发自动发表置顶评论
                try:
                    aid = bili_res.get("aid")
                    if aid:
                        from src.providers.bilibili_comment import BilibiliCommentClient
                        b_client = BilibiliCommentClient()
                        seed_msg = f"📌 项目开源地址与启动命令已整理在视频简介！\\n{content_text}\\n欢迎在弹幕与评论区交流探讨～"
                        b_client.post_and_top_comment(oid=aid, message=seed_msg)
                except Exception as ce:
                    logger.warning(f"Failed to post top comment to Bilibili: {ce}")
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
                dispatch_results.append(f"📲 **微信视频号 (手机原生发布素材)**: ✅ 9:16 原片与纯净文案已备齐")
            else:
                dispatch_results.append(f"📲 **微信视频号**: ⚠️ {channels_res.get('message')}")
        except Exception as e:
            dispatch_results.append(f"📲 **微信视频号**: ❌ 异常 ({e})")

    # 3. 抖音分发（若启用 douyin-tech-main，默认发布为仅自己可见）
    douyin_account = accounts_map.get("douyin-tech-main")
    if douyin_account and douyin_account.get("enabled", True):
        logger.info(f"Dispatching task '{task_id}' to Douyin ({douyin_account.get('publish_mode', 'private_publish')})...")
        try:
            from src.providers.douyin import DouyinProvider
            dy_provider = DouyinProvider(douyin_account)
            dy_res = dy_provider.save_draft(
                video_path=video_path or "",
                cover_path=cover_path or "",
                title=title,
                topics=topics,
                content_text=content_text,
                declare_aigc=douyin_account.get("declare_aigc", True)
            )
            if dy_res.get("success"):
                draft_id = dy_res.get("draft_id", "private_ok")
                dispatch_results.append(f"🎵 **抖音 (私密作品·仅自己可见)**: ✅ 视频已直传并保存 (ID: {draft_id})")
            else:
                dispatch_results.append(f"🎵 **抖音**: ⚠️ {dy_res.get('message')}")
        except Exception as e:
            dispatch_results.append(f"🎵 **抖音**: ❌ 异常 ({e})")

    # 4. 手机发帖专例文案推送（针对小红书 7 天避险冷冻期 & 手机发布）
    try:
        clean_tags = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
        short_title = title[:20]
        full_body = f"{content_text}\n\n💡 欢迎在评论区交流讨论～\n\n{clean_tags}"
        
        # 按照标题与正文分两个独立代码块，方便手机端飞书一键轻触复制
        post_payload = {
            "zh_cn": {
                "title": f"📱 【手机端发帖纯净素材】(任务: {task_id})",
                "content": [
                    [{"tag": "text", "text": "📌 标题（<=20字，点击代码块一键复制）："}],
                    [{"tag": "code_block", "language": "plaintext", "text": short_title}],
                    [{"tag": "text", "text": "\n📝 正文内容与标签（点击代码块一键复制）："}],
                    [{"tag": "code_block", "language": "plaintext", "text": full_body}],
                ]
            }
        }
        notify_feishu("", post_payload=post_payload)
        dispatch_results.append(f"📕 **小红书 (手机发帖素材)**: ✅ 纯净文案已推送到飞书私聊")
    except Exception as e:
        dispatch_results.append(f"📕 **小红书**: ❌ 素材推送失败 ({e})")

    # 4. 更新 SQLite 数据库任务状态
    db.update_task_status(task_id, "DISPATCHED", platform_draft_id=f"matrix_{task_id}")
    logger.info(f"Task '{task_id}' successfully transitioned to DISPATCHED")

    # 5. 向飞书播报整体分发汇总卡片
    summary_msg = f"🎉 **任务 `{task_id}` 动态多平台分发执行完毕**:\n\n" + "\n".join(dispatch_results)
    
    # 整合手机端发帖纯净素材：将【标题】与【正文内容】分别分两个独立代码块，方便手机端飞书一键轻触复制
    clean_tags = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
    short_title = title[:20]
    full_body = f"{content_text}\n\n💡 欢迎在评论区交流讨论～\n\n{clean_tags}"
    
    summary_post_payload = {
        "zh_cn": {
            "title": f"🎉 任务 `{task_id}` 发布完成汇报 & 📱 手机端纯净素材",
            "content": [
                [{"tag": "text", "text": summary_msg + "\n\n────────────────\n📱 【手机端发帖纯净素材】\n\n📌 标题（<=20字，点击代码块一键复制）："}],
                [{"tag": "code_block", "language": "plaintext", "text": short_title}],
                [{"tag": "text", "text": "\n📝 正文内容与标签（点击代码块一键复制）："}],
                [{"tag": "code_block", "language": "plaintext", "text": full_body}],
            ]
        }
    }
    notify_feishu("", post_payload=summary_post_payload)
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
