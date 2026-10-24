#!/usr/bin/env python3
"""
scripts/run_feishu_worker.py
Phase 2: 飞书长连接卡片审批工作台 Worker
通过 WebSocket 建立与飞书开放平台的双向长连接，监听交互卡片上的【一键批准】与【驳回重写】按钮动作。
自动更新 SQLite 状态机，并将卡片原位热更新为审核通过/已驳回状态。
"""

import os
import sys
import json
import logging
from dotenv import load_dotenv

# 确保 autocontent-pipeline 根目录在 sys.path 中
PIPELINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, PIPELINE_ROOT)

load_dotenv(os.path.join(PIPELINE_ROOT, ".env"))

import lark_oapi as lark
from lark_oapi.ws import Client as WsClient
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse
)
from src.db import PipelineDB
from src.feishu.card_builder import FeishuCardBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FeishuWorker")

db = PipelineDB(os.getenv("SQLITE_DB_PATH", os.path.join(PIPELINE_ROOT, "data/pipeline_state.db")))


def handle_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """
    处理卡片按钮点击回调动作
    """
    resp = P2CardActionTriggerResponse()
    try:
        event = getattr(data, "event", None)
        if not event:
            logger.warning("Empty event in card action trigger")
            resp.toast = {"type": "warning", "content": "收到空事件"}
            return resp

        # 解析动作参数
        action = getattr(event, "action", None)
        action_val = {}
        if action:
            val_raw = getattr(action, "value", None)
            if isinstance(val_raw, dict):
                action_val = val_raw
            elif isinstance(val_raw, str):
                try:
                    action_val = json.loads(val_raw)
                except Exception:
                    action_val = {}

        action_type = action_val.get("action")
        task_id = action_val.get("task_id")

        logger.info(f"Received card action: action_type={action_type}, task_id={task_id}")

        if not task_id:
            resp.toast = {"type": "error", "content": "缺少任务 ID"}
            return resp

        # 查询当前任务
        task = db.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in SQLite DB")
            resp.toast = {"type": "error", "content": f"未找到任务: {task_id}"}
            return resp

        draft_script = json.loads(task.get("draft_script_json") or "{}")
        title = draft_script.get("title", "未命名短视频")
        topics = draft_script.get("topics", [])
        content_text = draft_script.get("content_text", "")
        resource_keyword = task.get("resource_keyword") or draft_script.get("resource_keyword", "")
        fact_summary = task.get("fact_summary_150") or draft_script.get("fact_summary_150", "")

        # 估算视频时长与大小
        duration_sec = 28.0
        video_size_mb = 1.90
        video_path = task.get("video_path")
        if video_path and os.path.exists(video_path):
            video_size_mb = os.path.getsize(video_path) / (1024 * 1024)

        if action_type == "APPROVE_TASK":
            db.update_task_status(task_id, "APPROVED")
            logger.info(f"Task {task_id} status updated to APPROVED")
            resp.toast = {"type": "success", "content": "✅ 已批准！正在自动同步发布至抖音与小红书..."}

            # 原位更新卡片为已批准状态
            new_card = FeishuCardBuilder.build_approval_card(
                task_id=task_id,
                title=title,
                topics=topics,
                content_text=content_text,
                resource_keyword=resource_keyword,
                fact_summary=fact_summary,
                duration_sec=duration_sec,
                video_size_mb=video_size_mb,
                status="APPROVED"
            )
            resp.card = {"type": "raw", "data": new_card}

            # 触发后台进程执行发布上传
            import subprocess
            publish_script = os.path.join(str(PIPELINE_ROOT), "scripts", "run_publish_worker.py")
            subprocess.Popen([sys.executable, publish_script, "--task-id", task_id])

        elif action_type == "REJECT_TASK":
            db.update_task_status(task_id, "REJECTED")
            logger.info(f"Task {task_id} status updated to REJECTED")
            resp.toast = {"type": "info", "content": "❌ 已驳回该短视频脚本"}

            # 原位更新卡片为已驳回状态
            new_card = FeishuCardBuilder.build_approval_card(
                task_id=task_id,
                title=title,
                topics=topics,
                content_text=content_text,
                resource_keyword=resource_keyword,
                fact_summary=fact_summary,
                duration_sec=duration_sec,
                video_size_mb=video_size_mb,
                status="REJECTED",
                rejection_reason="审核人人工驳回"
            )
            resp.card = {"type": "raw", "data": new_card}

        else:
            logger.warning(f"Unknown action_type: {action_type}")
            resp.toast = {"type": "warning", "content": f"未知操作: {action_type}"}

    except Exception as e:
        logger.exception(f"Error handling card action: {e}")
        resp.toast = {"type": "error", "content": f"处理失败: {str(e)}"}

    return resp


def main():
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        logger.error("FEISHU_APP_ID or FEISHU_APP_SECRET is not configured!")
        sys.exit(1)

    logger.info("Initializing Lark WebSocket Client...")
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_card_action_trigger(handle_card_action) \
        .build()

    client = WsClient(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        auto_reconnect=True
    )

    logger.info(f"🚀 Feishu Approval WebSocket Worker started successfully for app_id: {app_id}")
    logger.info("Listening for card action triggers (APPROVE_TASK / REJECT_TASK)...")
    client.start()


if __name__ == "__main__":
    main()
