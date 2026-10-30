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
import time
import logging
from typing import Dict
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
from src.feishu.approval_service import FeishuApprovalService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FeishuWorker")

db = PipelineDB(os.getenv("SQLITE_DB_PATH", os.path.join(PIPELINE_ROOT, "data/pipeline_state.db")))
approval_service = FeishuApprovalService()

# 防抖锁与冷却时间（同一任务 5 秒内禁止并发重复触发）
DEBOUNCE_CACHE: Dict[str, float] = {}
DEBOUNCE_COOLDOWN_SEC = 5.0


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

        # 核心过滤：若为通用方案决策卡片（如 hermes_decision_card）或非本流水线动作，Worker 必须静默忽略，绝不拦截报错
        if action_val.get("hermes_decision_card") or action_type not in ("APPROVE_TASK", "REJECT_TASK", "PREPARE_MANUAL_PUBLISH", "COMPLETE_MANUAL_PUBLISH"):
            logger.debug(f"Ignoring non-pipeline action: {action_val}")
            return resp

        logger.info(f"Received pipeline card action: action_type={action_type}, task_id={task_id}")

        if not task_id:
            logger.warning(f"Pipeline action '{action_type}' missing task_id")
            resp.toast = {"type": "error", "content": "缺少任务 ID"}
            return resp

        # 防抖检查：拦截 5 秒内的多重点击
        now = time.time()
        last_click = DEBOUNCE_CACHE.get(task_id, 0.0)
        if (now - last_click) < DEBOUNCE_COOLDOWN_SEC:
            logger.info(f"Task {task_id} action debounced (within {DEBOUNCE_COOLDOWN_SEC}s)")
            resp.toast = {"type": "info", "content": "⚡ 任务正在后台处理中，请勿重复点击"}
            return resp
        DEBOUNCE_CACHE[task_id] = now

        # 查询当前任务
        task = db.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found in SQLite DB")
            resp.toast = {"type": "error", "content": f"未找到任务: {task_id}"}
            return resp

        draft_script = json.loads(task.get("draft_script_json") or "{}")
        title = draft_script.get("title", "未命名短视频")
        # 兼容 tags 和 topics
        topics = draft_script.get("tags") or draft_script.get("topics") or []
        # 兼容 content_text 和 fact_summary_150 / hook_narration
        content_text = draft_script.get("content_text") or draft_script.get("fact_summary_150") or ""
        resource_keyword = task.get("resource_keyword") or draft_script.get("resource_keyword", "")
        fact_summary = task.get("fact_summary_150") or draft_script.get("fact_summary_150", "")

        # 估算视频时长与大小
        duration_sec = 28.0
        video_size_mb = 1.90
        video_path = task.get("video_path")
        if video_path and os.path.exists(video_path):
            video_size_mb = os.path.getsize(video_path) / (1024 * 1024)

        # 读取任务实际套用的模板 template_id
        task_record = db.get_task(task_id) or {}
        aligned_json_str = task_record.get("aligned_render_json") or "{}"
        try:
            task_template_id = json.loads(aligned_json_str).get("template_id")
        except Exception:
            task_template_id = None

        open_message_id = None
        context_obj = getattr(event, "context", None)
        if context_obj:
            open_message_id = getattr(context_obj, "open_message_id", None)

        if action_type == "PREPARE_MANUAL_PUBLISH":
            db.update_task_status(task_id, "MANUAL_PUBLISHING")
            logger.info(f"Task {task_id} status updated to MANUAL_PUBLISHING")
            resp.toast = {"type": "success", "content": "📋 发帖素材已就绪！请长按复制文案并保存视频"}

            def async_send_manual_material():
                try:
                    # 1. 构造纯净发帖文案
                    clean_tags = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
                    pure_post_text = (
                        f"{title}\n\n"
                        f"{content_text}\n\n"
                        f"欢迎在评论区交流讨论～\n\n"
                        f"{clean_tags}"
                    )
                    
                    target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
                    rec_type = "open_id" if target.startswith("ou_") else "chat_id"

                    # 2. 推送一条纯净可复制文本消息
                    if approval_service:
                        approval_service.send_text_message(
                            receive_id=target,
                            receive_id_type=rec_type,
                            text=f"📱 【手机发帖专例文案·长按复制】\n━━━━━━━━━━━━━━\n{pure_post_text}\n━━━━━━━━━━━━━━"
                        )
                        logger.info(f"Successfully sent pure post text for task {task_id}")

                    # 3. 异步热更新原卡片状态为 MANUAL_PUBLISHING
                    new_card = FeishuCardBuilder.build_approval_card(
                        task_id=task_id,
                        title=title,
                        topics=topics,
                        content_text=content_text,
                        resource_keyword=resource_keyword,
                        fact_summary=fact_summary,
                        duration_sec=duration_sec,
                        video_size_mb=video_size_mb,
                        template_id=task_template_id,
                        status="MANUAL_PUBLISHING"
                    )
                    if open_message_id and approval_service:
                        approval_service.update_card(open_message_id, new_card)
                        logger.info(f"Updated card {open_message_id} to MANUAL_PUBLISHING")
                except Exception as mat_err:
                    logger.exception(f"Async send manual material failed for {task_id}: {mat_err}")

            import threading
            threading.Thread(target=async_send_manual_material, daemon=True).start()

        elif action_type == "COMPLETE_MANUAL_PUBLISH":
            db.update_task_status(task_id, "PUBLISHED")
            logger.info(f"Task {task_id} status updated to PUBLISHED (manual)")
            resp.toast = {"type": "success", "content": "🎉 祝贺！手机端发帖已成功归档"}

            def async_update_card_to_published():
                try:
                    new_card = FeishuCardBuilder.build_approval_card(
                        task_id=task_id,
                        title=title,
                        topics=topics,
                        content_text=content_text,
                        resource_keyword=resource_keyword,
                        fact_summary=fact_summary,
                        duration_sec=duration_sec,
                        video_size_mb=video_size_mb,
                        template_id=task_template_id,
                        status="PUBLISHED"
                    )
                    if open_message_id and approval_service:
                        approval_service.update_card(open_message_id, new_card)
                except Exception as patch_err:
                    logger.warning(f"Async card patch to PUBLISHED failed for {task_id}: {patch_err}")

            import threading
            threading.Thread(target=async_update_card_to_published, daemon=True).start()

        elif action_type == "APPROVE_TASK":
            db.update_task_status(task_id, "APPROVED")
            logger.info(f"Task {task_id} status updated to APPROVED")
            resp.toast = {"type": "success", "content": "✅ 已批准！正在自动同步发布至小红书..."}

            # 极速毫秒级响应：通过异步子线程热更新飞书卡片，避免在大 JSON 序列化与传输时触发前端 362 错误
            def async_update_card_to_approved():
                try:
                    new_card = FeishuCardBuilder.build_approval_card(
                        task_id=task_id,
                        title=title,
                        topics=topics,
                        content_text=content_text,
                        resource_keyword=resource_keyword,
                        fact_summary=fact_summary,
                        duration_sec=duration_sec,
                        video_size_mb=video_size_mb,
                        template_id=task_template_id,
                        status="APPROVED"
                    )
                    if open_message_id and approval_service:
                        approval_service.update_card(open_message_id, new_card)
                except Exception as patch_err:
                    logger.warning(f"Async card patch failed for {task_id}: {patch_err}")

            import threading
            threading.Thread(target=async_update_card_to_approved, daemon=True).start()

            # 纯异步解耦：立即触发独立后台进程执行上传，主事件循环毫秒级返回
            import subprocess
            publish_script = os.path.join(str(PIPELINE_ROOT), "scripts", "run_publish_worker.py")
            publish_log = os.path.join(str(PIPELINE_ROOT), "data", f"publish_{task_id}.log")
            log_f = open(publish_log, "a", encoding="utf-8")
            subprocess.Popen(
                [sys.executable, publish_script, "--task-id", task_id],
                stdout=log_f,
                stderr=log_f,
                start_new_session=True
            )

        elif action_type == "REJECT_TASK":
            db.update_task_status(task_id, "REJECTED")
            logger.info(f"Task {task_id} status updated to REJECTED")
            resp.toast = {"type": "info", "content": "❌ 已驳回该短视频脚本"}

            def async_update_card_to_rejected():
                try:
                    new_card = FeishuCardBuilder.build_approval_card(
                        task_id=task_id,
                        title=title,
                        topics=topics,
                        content_text=content_text,
                        resource_keyword=resource_keyword,
                        fact_summary=fact_summary,
                        duration_sec=duration_sec,
                        video_size_mb=video_size_mb,
                        template_id=task_template_id,
                        status="REJECTED",
                        rejection_reason="审核人人工驳回"
                    )
                    if open_message_id and approval_service:
                        approval_service.update_card(open_message_id, new_card)
                except Exception as patch_err:
                    logger.warning(f"Async card patch failed for {task_id}: {patch_err}")

            import threading
            threading.Thread(target=async_update_card_to_rejected, daemon=True).start()

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
