import os
import sys
import json
import asyncio
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import PipelineDB
from scripts.render_hotspot import render_hotspot_video
from src.feishu.card_builder import FeishuCardBuilder
from src.feishu.approval_service import FeishuApprovalService

TASK_ID = "news_gvs5h_20261025_150348"

async def main():
    print(f"🎬 开始按 T1 模板重新渲染待审核任务: {TASK_ID}")
    draft_path = PROJECT_ROOT / "data" / f"draft_{TASK_ID}.json"
    if not draft_path.exists():
        # 从 db 中提取并写回
        db = PipelineDB(PROJECT_ROOT / "data" / "pipeline_state.db")
        t = db.get_task(TASK_ID)
        draft_dict = json.loads(t["draft_script_json"])
        with open(draft_path, "w", encoding="utf-8") as f:
            json.dump(draft_dict, f, ensure_ascii=False, indent=2)
    else:
        with open(draft_path, "r", encoding="utf-8") as f:
            draft_dict = json.load(f)

    # 显式采用轮播首选 T1 editorial_news 进行正片重新生成
    video_path, cover_path, output_dir, duration = await render_hotspot_video(
        str(draft_path), TASK_ID, template="editorial_news"
    )

    db = PipelineDB(PROJECT_ROOT / "data" / "pipeline_state.db")
    db.update_task_status(
        TASK_ID,
        "WAITING_APPROVAL",
        video_path=video_path,
        cover_path=cover_path,
        xhs_cards_dir=os.path.join(output_dir, "xhs_cards")
    )
    print(f"✅ 视频重新渲染完成: {video_path}")

    # 推送至飞书
    service = FeishuApprovalService()
    video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    card = FeishuCardBuilder.build_approval_card(
        task_id=TASK_ID,
        title=draft_dict.get("title", ""),
        topics=draft_dict.get("tags", []),
        content_text=draft_dict.get("fact_summary_150", ""),
        resource_keyword=draft_dict.get("resource_keyword", "1024"),
        fact_summary=draft_dict.get("fact_summary_150", ""),
        duration_sec=duration,
        video_size_mb=video_size_mb,
        status="PENDING_APPROVAL"
    )

    target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
    rec_type = "open_id" if target.startswith("ou_") else "chat_id"

    # 1. 发送最新成片原片
    v_msg = service.upload_and_send_video(receive_id=target, receive_id_type=rec_type, video_path=video_path)
    print(f"Feishu video preview dispatched: {v_msg}")

    # 2. 发送富媒体交互卡片（一键批准 / 驳回）
    msg_id = service.send_approval_card(receive_id=target, receive_id_type=rec_type, card_content=card)
    print(f"Feishu review card dispatched: {msg_id}")

if __name__ == "__main__":
    asyncio.run(main())
