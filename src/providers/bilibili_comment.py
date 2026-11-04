"""
src/providers/bilibili_comment.py
Bilibili 评论区互动与自动回复 Provider
基于官方 REST API 实现：
1. fetch_latest_replies: 获取创作者收到的最新未读回帖/评论
2. post_reply: 针对指定评论发表二级回复
3. 会话自动装配与 CSRF 校验
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("BilibiliComment")

PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
COOKIE_PATH = PIPELINE_ROOT / "data" / "cookies_bilibili.json"

class BilibiliCommentClient:
    def __init__(self, cookie_file: Optional[Path] = None):
        self.cookie_file = cookie_file or COOKIE_PATH
        self.cookies_dict = self._load_cookies()
        self.cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies_dict.items() if isinstance(v, (str, int))])
        self.csrf = self.cookies_dict.get("bili_jct", "")

    def _load_cookies(self) -> Dict[str, Any]:
        if not self.cookie_file.exists():
            logger.warning(f"Bilibili cookies file not found at {self.cookie_file}")
            return {}
        try:
            with open(self.cookie_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                elif isinstance(data, list):
                    return {c["name"]: c["value"] for c in data if "name" in c and "value" in c}
        except Exception as e:
            logger.error(f"Failed to load Bilibili cookies: {e}")
        return {}

    def is_available(self) -> bool:
        return bool(self.csrf and self.cookie_str)

    def fetch_latest_replies(self, recent_oids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        获取创作者收到的最新评论/回帖：
        双源聚合策略：
        1. 优先读取主消息信箱 (/x/msgfeed/reply)
        2. 补充主动抓取活跃稿件的一级直属评论 (/x/v2/reply/main)，杜绝信箱缓存延迟漏单
        :return: 结构化去重评论列表
        """
        if not self.is_available():
            logger.warning("Bilibili cookies or CSRF missing, cannot fetch comments.")
            return []

        results: List[Dict[str, Any]] = []
        seen_rpids = set()

        # Source 1: 全局消息中心通知
        url = "https://api.bilibili.com/x/msgfeed/reply"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": "https://message.bilibili.com"
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("code") == 0:
                    raw_items = data.get("data", {}).get("items", [])
                    for entry in raw_items:
                        item = entry.get("item", {})
                        user = entry.get("user", {})
                        rpid = item.get("source_id")
                        oid = item.get("subject_id")
                        comment_text = (item.get("source_content") or "").strip()
                        if rpid and oid and comment_text and rpid not in seen_rpids:
                            seen_rpids.add(rpid)
                            results.append({
                                "event_id": str(entry.get("id")),
                                "oid": oid,
                                "rpid": rpid,
                                "root_id": item.get("root_id", 0),
                                "business_id": item.get("business_id", 1),
                                "comment": comment_text,
                                "user": user.get("nickname") or "B站粉丝",
                                "user_mid": user.get("mid"),
                                "feed_title": item.get("title") or "B站视频作品",
                                "feed_url": item.get("uri") or f"https://www.bilibili.com/video/av{oid}",
                                "reply_time": entry.get("reply_time", 0),
                                "platform": "bilibili"
                            })
        except Exception as e:
            logger.error(f"Error fetching msgfeed replies: {e}")

        # Source 2: 活跃稿件主动巡检直属评论
        target_oids = recent_oids or [117556694223580]
        for aid in target_oids:
            try:
                main_url = f"https://api.bilibili.com/x/v2/reply/main?oid={aid}&type=1&mode=3&ps=20"
                main_req = urllib.request.Request(main_url, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Cookie": self.cookie_str,
                    "Referer": f"https://www.bilibili.com/video/av{aid}"
                })
                with urllib.request.urlopen(main_req, timeout=6) as resp:
                    main_data = json.loads(resp.read().decode("utf-8"))
                    if main_data.get("code") == 0:
                        rep_list = main_data.get("data", {}).get("replies") or []
                        for r in rep_list:
                            rpid = r.get("rpid")
                            if rpid and rpid not in seen_rpids:
                                seen_rpids.add(rpid)
                                results.append({
                                    "event_id": f"main_{rpid}",
                                    "oid": aid,
                                    "rpid": rpid,
                                    "root_id": rpid,
                                    "business_id": 1,
                                    "comment": r.get("content", {}).get("message", ""),
                                    "user": r.get("member", {}).get("uname") or "B站粉丝",
                                    "user_mid": r.get("member", {}).get("mid"),
                                    "feed_title": "B站活跃视频",
                                    "feed_url": f"https://www.bilibili.com/video/av{aid}",
                                    "reply_time": r.get("ctime", 0),
                                    "platform": "bilibili"
                                })
            except Exception as e:
                logger.error(f"Error checking active video comments for oid {aid}: {e}")

        return results

    def add_top_level_comment(self, oid: int, message: str) -> Dict[str, Any]:
        """
        向指定稿件发表一级主评论（冷启动首评）
        :param oid: 稿件目标 ID (aid)
        :param message: 评论正文
        :return: {"success": bool, "code": int, "message": str, "rpid": int}
        """
        if not self.is_available():
            return {"success": False, "message": "CSRF / Cookies not available"}

        url = "https://api.bilibili.com/x/v2/reply/add"
        payload = {
            "oid": str(oid),
            "type": "1", # 1: 视频
            "message": message,
            "csrf": self.csrf
        }
        data_encoded = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": f"https://www.bilibili.com/video/av{oid}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        try:
            req = urllib.request.Request(url, data=data_encoded, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                code = res.get("code", -1)
                msg = res.get("message", "")
                if code == 0:
                    new_rpid = res.get("data", {}).get("rpid")
                    logger.info(f"Successfully posted top-level comment to Bilibili video av{oid} (new rpid={new_rpid})")
                    return {"success": True, "code": 0, "message": msg, "rpid": new_rpid}
                else:
                    logger.warning(f"Bilibili post comment API returned error code {code}: {msg}")
                    return {"success": False, "code": code, "message": msg}
        except Exception as e:
            logger.error(f"Failed to post top-level comment to Bilibili video av{oid}: {e}")
            return {"success": False, "message": str(e)}

    def top_comment(self, oid: int, rpid: int, action: int = 1) -> Dict[str, Any]:
        """
        置顶或取消置顶指定评论
        :param oid: 稿件目标 ID (aid)
        :param rpid: 目标评论 ID
        :param action: 1: 置顶, 0: 取消置顶
        :return: {"success": bool, "code": int, "message": str}
        """
        if not self.is_available():
            return {"success": False, "message": "CSRF / Cookies not available"}

        url = "https://api.bilibili.com/x/v2/reply/top"
        payload = {
            "oid": str(oid),
            "type": "1", # 1: 视频
            "rpid": str(rpid),
            "action": str(action),
            "csrf": self.csrf
        }
        data_encoded = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": f"https://www.bilibili.com/video/av{oid}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        try:
            req = urllib.request.Request(url, data=data_encoded, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                code = res.get("code", -1)
                msg = res.get("message", "")
                if code == 0:
                    logger.info(f"Successfully pinned Bilibili comment {rpid} on av{oid}")
                    return {"success": True, "code": 0, "message": msg}
                else:
                    logger.warning(f"Bilibili top comment API returned error code {code}: {msg}")
                    return {"success": False, "code": code, "message": msg}
        except Exception as e:
            logger.error(f"Failed to pin Bilibili comment {rpid} on av{oid}: {e}")
            return {"success": False, "message": str(e)}

    def post_and_top_comment(self, oid: int, message: str) -> Dict[str, Any]:
        """
        原子化复合动作：发表一级主评论并自动将其置顶
        :param oid: 稿件目标 ID (aid)
        :param message: 评论正文
        :return: {"success": bool, "rpid": int, "top_success": bool}
        """
        add_res = self.add_top_level_comment(oid, message)
        if not add_res.get("success"):
            return add_res
        
        rpid = add_res.get("rpid")
        if not rpid:
            return add_res
        top_res = self.top_comment(oid, int(rpid), action=1)
        return {
            "success": True,
            "rpid": rpid,
            "top_success": top_res.get("success", False),
            "message": "Comment posted and pinned successfully" if top_res.get("success") else "Comment posted, top pending"
        }

    def reply_comment(self, oid: int, rpid: int, message: str, root_id: int = 0) -> Dict[str, Any]:
        """
        向指定评论发表回复
        :param oid: 稿件目标 ID (aid)
        :param rpid: 目标评论 ID
        :param message: 回复内容
        :param root_id: 根评论 ID（0 表示对一级评论回复，>0 表示对子楼层回复）
        :return: {"success": bool, "code": int, "message": str, "rpid": int}
        """
        if not self.is_available():
            return {"success": False, "message": "CSRF / Cookies not available"}

        url = "https://api.bilibili.com/x/v2/reply/add"
        root_val = rpid if (root_id == 0 or root_id is None) else root_id
        payload = {
            "oid": str(oid),
            "type": "1", # 1: 视频
            "message": message,
            "root": str(root_val),
            "parent": str(rpid),
            "csrf": self.csrf
        }
        data_encoded = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": f"https://www.bilibili.com/video/av{oid}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        try:
            req = urllib.request.Request(url, data=data_encoded, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=8) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                code = res.get("code", -1)
                msg = res.get("message", "")
                if code == 0:
                    new_rpid = res.get("data", {}).get("rpid")
                    logger.info(f"Successfully replied to Bilibili comment {rpid} (new rpid={new_rpid})")
                    return {"success": True, "code": 0, "message": msg, "rpid": new_rpid}
                else:
                    logger.warning(f"Bilibili reply API returned error code {code}: {msg}")
                    return {"success": False, "code": code, "message": msg}
        except Exception as e:
            logger.error(f"HTTP exception during Bilibili reply: {e}")
            return {"success": False, "message": str(e)}
