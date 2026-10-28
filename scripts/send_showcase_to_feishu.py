import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.feishu.approval_service import FeishuApprovalService

TASK_ID = "news_gvs5h_20261025_150348"
OUTPUT_BASE = PROJECT_ROOT / "output" / "template_showcase" / TASK_ID
target = "ou_47ff8c422d053a35c5eea55d3d75c209"

service = FeishuApprovalService()

TEMPLATES = [
    ("editorial_news", "T1 · 瑞士先锋报刊风 (Editorial Paper)", "turquoise"),
    ("cupertino_glass", "T2 · Apple 毛玻璃流光风 (Cupertino Glass)", "blue"),
    ("neo_brutalism", "T3 · 多巴胺波普粗野风 (Neo-Brutalism Pop)", "orange"),
    ("notion_craft", "T4 · Notion 暖调手账风 (Notion Warm Craft)", "carmine"),
    ("linear_obsidian", "T5 · Linear 钛黑流光风 (Linear Obsidian)", "indigo"),
]

for tpl_id, tpl_name, color in TEMPLATES:
    video_p = OUTPUT_BASE / tpl_id / f"video_{tpl_id}.mp4"
    cover_p = OUTPUT_BASE / tpl_id / f"cover_{tpl_id}.png"
    print(f"Sending {tpl_id}...")

    # 1. 发送模板说明卡片
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🎨 模板效果对比：{tpl_name}"},
            "template": color
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**模板标识**: `{tpl_id}`\n**居中对齐修复**: ✅ 底部动态字幕条已居中对齐（避让底部安全区），分镜卡片与顶部徽章已严密居中。\n\n👇 **下方紧跟该模板渲染成片视频**："
                }
            }
        ]
    }
    msg_card = service.send_approval_card(receive_id=target, receive_id_type="open_id", card_content=card_content)

    # 2. 发送视频
    v_msg = service.upload_and_send_video(
        receive_id=target,
        receive_id_type="open_id",
        video_path=str(video_p),
        file_name=f"{tpl_id}_{TASK_ID}.mp4"
    )
    print(f"Done {tpl_id}: card={msg_card}, video={v_msg}")

print("\n🎉 全部 5 套模板视频已全部推送至飞书！")
