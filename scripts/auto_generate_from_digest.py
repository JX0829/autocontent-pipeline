import asyncio
import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.hotspot_extractor import pick_and_convert_hotspot
from scripts.render_hotspot import render_hotspot_video

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AutoPipelineRunner")

async def trigger_video_from_digest(news_content: str = None, digest_file: str = None):
    """
    接收 2 小时热点总结内容或读取最近的新闻记录，从中挑选最强的一条科技热点生成短视频
    """
    if not news_content:
        if digest_file and os.path.exists(digest_file):
            with open(digest_file, "r", encoding="utf-8") as f:
                news_content = f.read()
        else:
            # 默认读取 data/hot_news_sent.json
            history_file = PROJECT_ROOT.parent / "data" / "hot_news_sent.json"
            if not history_file.exists():
                history_file = PROJECT_ROOT / "data" / "hot_news_sent.json"
            if history_file.exists():
                with open(history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        news_content = "\n".join([f"- {item.get('title', item)}" for item in data[-10:]])
                    elif isinstance(data, dict):
                        news_content = "\n".join([f"- {k}: {v}" for k, v in list(data.items())[-10:]])

    if not news_content or not news_content.strip():
        logger.warning("No news content provided for video generation.")
        return None

    logger.info("Extracting top tech hotspot from digest...")
    draft_dict = pick_and_convert_hotspot(news_content)
    task_id = draft_dict["task_id"]
    logger.info(f"Selected hotspot topic: '{draft_dict.get('title')}' (Task ID: {task_id})")

    # 保存 draft json
    draft_path = PROJECT_ROOT / "data" / f"draft_{task_id}.json"
    with open(draft_path, "w", encoding="utf-8") as f:
        json.dump(draft_dict, f, ensure_ascii=False, indent=2)

    logger.info(f"Rendering short video for task: {task_id}...")
    video_path, cover_path, output_dir, duration, resolved_tpl = await render_hotspot_video(str(draft_path), task_id)
    logger.info(f"Video rendered successfully! Path: {video_path}, Template: {resolved_tpl}")

    # 渲染完成后自动向飞书推送待审交互卡片
    try:
        from src.feishu.card_builder import FeishuCardBuilder
        from src.feishu.approval_service import FeishuApprovalService
        service = FeishuApprovalService()
        video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        card = FeishuCardBuilder.build_approval_card(
            task_id=task_id,
            title=draft_dict.get("title", ""),
            topics=draft_dict.get("tags", []),
            content_text=draft_dict.get("fact_summary_150", ""),
            resource_keyword=draft_dict.get("resource_keyword", "1024"),
            fact_summary=draft_dict.get("fact_summary_150", ""),
            duration_sec=duration,
            video_size_mb=video_size_mb,
            template_id=resolved_tpl,
            status="PENDING_APPROVAL"
        )
        target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_47ff8c422d053a35c5eea55d3d75c209")
        rec_type = "open_id" if target.startswith("ou_") else "chat_id"

        # 1. 先直发原生 mp4 视频原片至对话，供人工全屏播放复核
        video_msg_id = service.upload_and_send_video(receive_id=target, receive_id_type=rec_type, video_path=video_path)
        logger.info(f"Feishu video preview dispatched, video_msg_id={video_msg_id}")

        # 2. 紧随其后发送富媒体交互审批卡片（一键批准 / 驳回）
        msg_id = service.send_approval_card(receive_id=target, receive_id_type=rec_type, card_content=card)
        logger.info(f"Feishu review card dispatched, msg_id={msg_id}")
    except Exception as e:
        logger.warning(f"Failed to push feishu review card: {e}")

    return {
        "task_id": task_id,
        "title": draft_dict.get("title"),
        "video_path": video_path,
        "cover_path": cover_path,
        "output_dir": output_dir,
        "duration": duration
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trigger video generation from hotspot digest")
    parser.add_argument("--content", type=str, default=None, help="Raw text of 2-hour hotspot news")
    parser.add_argument("--file", type=str, default=None, help="Path to file containing hotspot digest")
    args = parser.parse_args()

    result = asyncio.run(trigger_video_from_digest(args.content, args.file))
    if result:
        print(f"\nSUCCESS: Generated video for '{result['title']}' at {result['video_path']}")
