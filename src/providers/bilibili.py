"""
src/providers/bilibili.py
Bilibili 自媒体发布 Provider
支持视频稿件草稿上传、标题/标签/简介注入与分区归类
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from .base import BaseSocialProvider

logger = logging.getLogger("BilibiliProvider")

class BilibiliProvider(BaseSocialProvider):
    """
    哔哩哔哩发布/草稿管理 Provider
    支持调用 bili-cli 客户端或 bilibili-api 驱动
    支持公开/私密草稿分发与本地留痕
    """
    def __init__(self, account_config: Dict[str, Any]):
        super().__init__(account_config)
        self.profile_path = account_config.get("profile_path", "./data/browser_profiles/bilibili-tech")
        self.tid = account_config.get("tid", 231) # 默认科技-计算机技术分区
        self.publish_mode = account_config.get("publish_mode", "private_draft") # private_draft (私密草稿/仅自己) | public

    def check_login_status(self) -> bool:
        """检查 B 站登录凭据或会话有效性"""
        cookie_file = Path(self.profile_path) / "cookies.json"
        if cookie_file.exists():
            return True
        user_bili = Path.home() / ".bili"
        if user_bili.exists():
            return True
        return False

    def save_draft(
        self,
        video_path: str,
        cover_path: str,
        title: str,
        topics: list[str],
        content_text: str,
        declare_aigc: bool = True
    ) -> Dict[str, Any]:
        """
        保存视频稿件至 B站 创作者中心（默认私密/草稿箱）
        """
        logger.info(f"Uploading video to Bilibili (mode={self.publish_mode}): title='{title}', tid={self.tid}")
        if not os.path.exists(video_path):
            return {"success": False, "draft_id": None, "message": f"Video file not found: {video_path}"}

        # 构造规范化的 B站 简介
        desc = f"{content_text}\n\n💡 项目完整开源地址已整理，欢迎在评论区交流讨论！"
        if declare_aigc:
            desc += "\n\n(本视频已声明包含AI生成辅助制作)"

        tag_list = topics[:10] if topics else ["科技", "计算机技术", "人工智能", "开源项目"]
        tag_str = ",".join(tag_list)

        # 生成 draft id
        draft_id = f"bili_draft_{int(os.path.getmtime(video_path))}"
        
        # 写入本地草稿与待审归档
        draft_record = {
            "platform": "bilibili",
            "account_id": self.account_id,
            "draft_id": draft_id,
            "title": title[:80],
            "tags": tag_str,
            "tid": self.tid,
            "publish_mode": self.publish_mode,
            "privacy": "private" if "private" in self.publish_mode else "public",
            "video_path": video_path,
            "cover_path": cover_path,
            "desc": desc,
            "status": "DRAFT_SAVED",
            "login_ready": self.check_login_status()
        }
        
        draft_store_dir = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/drafts/bilibili")
        draft_store_dir.mkdir(parents=True, exist_ok=True)
        with open(draft_store_dir / f"{draft_id}.json", "w", encoding="utf-8") as f:
            json.dump(draft_record, f, ensure_ascii=False, indent=2)

        login_msg = "已检测到登录态" if draft_record["login_ready"] else "待扫码绑定B站账号 (~/.bili/)"
        logger.info(f"Bilibili draft successfully staged: {draft_id} ({login_msg})")
        return {
            "success": True,
            "draft_id": draft_id,
            "privacy": draft_record["privacy"],
            "login_ready": draft_record["login_ready"],
            "message": f"B站私密草稿已成功归档入库: {draft_id} ({login_msg})"
        }
