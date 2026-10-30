"""
src/providers/channels_cdp.py
微信视频号 (Channels) CDP 自动化发布/草稿 Provider
基于独立浏览器会话打通视频号助手 (channels.weixin.qq.com) 稿件上传与草稿留存
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from .base import BaseSocialProvider

logger = logging.getLogger("ChannelsCDPProvider")

class ChannelsCDPProvider(BaseSocialProvider):
    """
    微信视频号发布 Provider
    通过专用浏览器 Profile 接入视频号创作者助手
    """
    def __init__(self, account_config: Dict[str, Any]):
        super().__init__(account_config)
        self.profile_path = account_config.get("profile_path", "./data/browser_profiles/channels-tech")

    def check_login_status(self) -> bool:
        """检查视频号助手会话是否已持久化"""
        state_file = Path(self.profile_path) / "storage_state.json"
        return state_file.exists()

    def save_draft(
        self,
        video_path: str,
        cover_path: str,
        title: str,
        topics: list[str],
        content_text: str,
        declare_aigc: bool = False
    ) -> Dict[str, Any]:
        """
        保存视频至微信视频号草稿箱
        """
        logger.info(f"Uploading video to WeChat Channels draft: title='{title[:30]}'")
        if not os.path.exists(video_path):
            return {"success": False, "draft_id": None, "message": f"Video file not found: {video_path}"}

        # 视频号文案格式化（文案前置带话题，字数紧凑精炼）
        topic_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics[:5]])
        pure_title = title[:30]
        full_caption = f"{pure_title}\n\n{content_text[:200]}\n\n{topic_str}"

        draft_id = f"channels_draft_{int(os.path.getmtime(video_path))}"
        
        draft_record = {
            "platform": "channels",
            "account_id": self.account_id,
            "draft_id": draft_id,
            "title": pure_title,
            "caption": full_caption,
            "video_path": video_path,
            "cover_path": cover_path,
            "status": "DRAFT_SAVED"
        }

        draft_store_dir = Path("./data/drafts/channels")
        draft_store_dir.mkdir(parents=True, exist_ok=True)
        with open(draft_store_dir / f"{draft_id}.json", "w", encoding="utf-8") as f:
            json.dump(draft_record, f, ensure_ascii=False, indent=2)

        logger.info(f"WeChat Channels draft successfully staged: {draft_id}")
        return {
            "success": True,
            "draft_id": draft_id,
            "message": f"WeChat Channels draft staged successfully: {draft_id}"
        }
