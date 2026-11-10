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
        cookie_file = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/cookies_bilibili.json")
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

        # 构造规范化的 B站 简介（合规铁律：严禁引流词，结尾统一仅此一句）
        desc = f"{content_text}\n\n欢迎在评论区交流讨论～"
        if declare_aigc:
            desc += "\n\n(本视频包含AI辅助生成内容，请理性甄别)"

        tag_list = topics[:10] if topics else ["科技", "计算机技术", "人工智能", "开源项目"]
        tag_str = ",".join(tag_list)

        # 判断可见性与私密模式（必须在上传前确定，确保 --only-self 真正生效）
        is_private = any(k in self.publish_mode.lower() for k in ["private", "self", "draft", "hidden"])
        privacy_val = "only_self" if is_private else "public"

        # 检查是否具备真实 B站 Credential 凭据以支持直传线上
        cookie_file = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/cookies_bilibili.json")
        real_upload_res = None
        if cookie_file.exists():
            try:
                import subprocess
                upload_script = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/scripts/bilibili_live_uploader.py")
                if upload_script.exists():
                    cmd = [
                        "/Users/a1/.agent-reach/tools/bili-cli/venv/bin/python3",
                        str(upload_script),
                        "--video", video_path,
                        "--cover", cover_path,
                        "--title", title,
                        "--desc", desc,
                        "--tags", tag_str,
                        "--tid", str(self.tid)
                    ]
                    if is_private:
                        cmd.append("--only-self")
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if p.returncode != 0:
                        logger.error(f"Bilibili real upload failed: rc={p.returncode} stderr={p.stderr[-600:]}")
                        return {
                            "success": False,
                            "error": "BILI_UPLOAD_FAILED",
                            "stderr": p.stderr[-800:],
                            "message": "B站真实上传失败，未产生稿件（拒绝虚报成功）"
                        }
                    real_data = json.loads(p.stdout.strip().splitlines()[-1])
                    payload = real_data.get("data") if isinstance(real_data.get("data"), dict) else real_data
                    bvid = (payload or {}).get("bvid") or real_data.get("bvid")
                    aid = (payload or {}).get("aid") or real_data.get("aid")
                    code = real_data.get("code", 0)
                    if code not in (0, None) or not bvid:
                        return {
                            "success": False,
                            "error": "BILI_NO_BVID",
                            "raw": real_data,
                            "message": f"B站上传未返回有效 bvid（code={code}），判定失败"
                        }
                    real_upload_res = {"bvid": bvid, "aid": aid, "raw": real_data}
                    logger.info(f"Real Bilibili upload successful: bvid={bvid} aid={aid}")

                    # 核心新增：自动发表置顶评论闭环 (Pinned Seed Comment)
                    if aid:
                        try:
                            from .bilibili_comment import BilibiliCommentClient
                            c_client = BilibiliCommentClient(cookie_file)
                            seed_msg = f"{content_text[:120]}\n\n欢迎在评论区交流讨论～"
                            pin_res = c_client.post_and_top_comment(int(aid), seed_msg)
                            logger.info(f"Bilibili automatic pinned comment result: {pin_res}")
                        except Exception as ce:
                            logger.warning(f"Failed to post automatic pinned comment: {ce}")
            except Exception as e:
                logger.exception(f"Real Bilibili upload exception: {e}")
                return {
                    "success": False,
                    "error": "BILI_UPLOAD_EXCEPTION",
                    "message": f"B站上传异常：{e}"
                }

        if not real_upload_res:
            return {
                "success": False,
                "error": "BILI_NO_CREDENTIAL",
                "message": "缺少 B站登录凭据 data/cookies_bilibili.json，未执行上传"
            }

        draft_id = real_upload_res.get("bvid")

        # 写入本地草稿与待审归档
        draft_record = {
            "platform": "bilibili",
            "account_id": self.account_id,
            "draft_id": draft_id,
            "title": title[:80],
            "tags": tag_str,
            "tid": self.tid,
            "publish_mode": self.publish_mode,
            "privacy": privacy_val,
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
