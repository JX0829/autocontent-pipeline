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
        self.publish_mode = account_config.get("publish_mode", "public") # public (公开分发) | draft (草稿箱)

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
        declare_aigc: bool = False,
        declare_original: bool = True
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
        intent = "public" if self.publish_mode == "public" else "draft"

        draft_record = {
            "platform": "channels",
            "account_id": self.account_id,
            "draft_id": draft_id,
            "title": pure_title,
            "caption": full_caption,
            "video_path": video_path,
            "cover_path": cover_path,
            "publish_mode": self.publish_mode,
            # 如实标注：本 provider 无真实上传实现，只是素材就绪
            "distribution": f"pending_manual_{intent}",
            "status": "MATERIAL_READY",
            "published": False,
            "publish_target": "channels_manual_upload",
            "declare_original": declare_original,
            "declare_aigc": declare_aigc,
            "login_ready": self.check_login_status()
        }

        draft_store_dir = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/drafts/channels")
        draft_store_dir.mkdir(parents=True, exist_ok=True)
        with open(draft_store_dir / f"{draft_id}.json", "w", encoding="utf-8") as f:
            json.dump(draft_record, f, ensure_ascii=False, indent=2)

        # ⚠️ 本 provider 目前**只生成发布素材记录，不做真实上传**（无任何浏览器操作）。
        #    历史实现返回 status=PUBLIC_DISPATCHED + "公开分发已就绪"，属虚报成功：
        #    调用方会误以为视频已在视频号公开且已勾原创，实际平台侧什么都没发生。
        #    与项目铁律一致（状态只能代表平台侧已确认），此处如实降级为「素材就绪」。
        #    真实 CDP 发布（含原创/AI 声明勾选）需单独立项，完成前不得声称已发布。
        flags = []
        if declare_original:
            flags.append("原创声明")
        if declare_aigc:
            flags.append("AI 声明")
        flag_desc = f"，待勾选：{'/'.join(flags)}" if flags else ""

        mode_desc = f"微信视频号素材已就绪（未真实发布，需人工上传{flag_desc}）"
        logger.warning(f"[CHANNELS] {mode_desc}: {draft_id} — 本 provider 无真实上传实现")

        return {
            "success": True,               # 素材生成成功
            "published": False,            # 但**未发布**，调用方必须据此判断
            "draft_id": draft_id,
            "distribution": draft_record["distribution"],
            "status": draft_record["status"],
            "login_ready": draft_record["login_ready"],
            "declare_original": declare_original,
            "declare_aigc": declare_aigc,
            "message": f"{mode_desc}: {draft_id}"
        }
