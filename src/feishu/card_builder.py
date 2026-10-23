import json
from typing import Dict, Any, Optional

class FeishuCardBuilder:
    """
    飞书交互式卡片构造器
    用于生成短视频待审卡片（含封面、标题、话题、文案、分镜简述及一键批准/驳回操作按钮）
    """
    
    @staticmethod
    def build_approval_card(
        task_id: str,
        title: str,
        topics: list[str],
        content_text: str,
        resource_keyword: str,
        fact_summary: str,
        duration_sec: float,
        video_size_mb: float,
        status: str = "PENDING_APPROVAL",
        rejection_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        构建带有交互按钮的审批卡片 JSON
        """
        topic_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
        
        status_badges = {
            "PENDING_APPROVAL": ("🟠 待人工审核", "orange"),
            "APPROVED": ("🟢 审核通过 · 待上传草稿", "green"),
            "REJECTED": ("🔴 已驳回", "red"),
        }
        badge_text, badge_color = status_badges.get(status, ("⚪ 未知状态", "grey"))

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**状态**: {badge_text}  |  **时长**: `{duration_sec:.1f}s`  |  **视频大小**: `{video_size_mb:.2f} MB`\n**任务 ID**: `{task_id}`"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📌 **视频标题**:\n**{title}**\n\n🏷️ **话题标签**: {topic_str}\n\n🔑 **私域引流暗号**: `{resource_keyword}`"
                }
            },
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📝 **核心正文/文案**:\n{content_text}"
                }
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"💡 事实核查依据: {fact_summary}"
                    }
                ]
            }
        ]

        if status == "REJECTED" and rejection_reason:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"⚠️ **驳回原因**: {rejection_reason}"
                }
            })

        # 仅在待审批状态下展示操作按钮
        if status == "PENDING_APPROVAL":
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": "✅ 一键批准 (进入草稿箱)"
                        },
                        "type": "primary",
                        "value": {
                            "action": "APPROVE_TASK",
                            "task_id": task_id
                        }
                    },
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": "❌ 驳回重写"
                        },
                        "type": "danger",
                        "value": {
                            "action": "REJECT_TASK",
                            "task_id": task_id
                        }
                    }
                ]
            })

        card = {
            "config": {
                "wide_screen_mode": True,
                "update_multi": True
            },
            "header": {
                "template": "blue" if status == "PENDING_APPROVAL" else ("green" if status == "APPROVED" else "red"),
                "title": {
                    "tag": "plain_text",
                    "content": f"🎬 短视频待审工作台 · {title[:20]}"
                }
            },
            "elements": elements
        }
        return card
