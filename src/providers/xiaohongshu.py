import os
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional
from src.providers.base import BaseSocialProvider

logger = logging.getLogger("XiaohongshuProvider")

class XiaohongshuProvider(BaseSocialProvider):
    """
    小红书发布与互动 Provider（基于 xiaohongshu-mcp 服务）
    """
    def __init__(self, account_config: Dict[str, Any]):
        super().__init__(account_config)
        self.mcp_url = account_config.get("mcp_url", "http://localhost:18060/mcp")
        self.session_id: Optional[str] = None

    def _call_mcp(self, method: str, params: Dict[str, Any], max_retries: int = 3) -> Dict[str, Any]:
        """
        调用 MCP 接口，内置网络抖动指数退避重试机制（最多重试 3 次）
        """
        import time
        headers = ["-H", "Content-Type: application/json"]
        if self.session_id:
            headers.extend(["-H", f"Mcp-Session-Id: {self.session_id}"])
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        cmd = ["curl", "-s", "--connect-timeout", "5", "--max-time", "60", "-i", "-X", "POST", self.mcp_url] + headers + ["-d", data]

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                res = subprocess.check_output(cmd).decode()
                header_part, body_part = res.split("\r\n\r\n", 1)
                for line in header_part.split("\r\n"):
                    if line.lower().startswith("mcp-session-id:"):
                        self.session_id = line.split(":", 1)[1].strip()
                return json.loads(body_part.strip())
            except Exception as e:
                last_error = e
                logger.warning(f"MCP Call attempt {attempt}/{max_retries} failed for '{method}': {e}")
                if attempt < max_retries:
                    time.sleep(attempt * 1.5)

        logger.error(f"All {max_retries} MCP call attempts failed for '{method}': {last_error}")
        return {"error": {"code": -32000, "message": f"Network retry exhausted: {str(last_error)}"}}

    def _ensure_session(self):
        if not self.session_id:
            init_res = self._call_mcp("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "autocontent-pipeline", "version": "1.0"}
            })
            if "error" in init_res:
                logger.error(f"Failed to initialize Xiaohongshu MCP: {init_res['error']}")

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_session()
        resp = self._call_mcp("tools/call", {
            "name": tool_name,
            "arguments": arguments
        })
        if "error" in resp:
            logger.error(f"MCP Tool call '{tool_name}' failed: {resp['error']}")
            return {"success": False, "error": resp["error"]}

        res_payload = resp.get("result", {})
        is_error = res_payload.get("isError", False)
        result_content = res_payload.get("content", [])
        text_out = ""
        for c in result_content:
            if c.get("type") == "text":
                text_out += c.get("text", "")

        # 严格防御：若返回包含 isError 或显式失败提示，判定为调用失败
        if is_error or "失败" in text_out or "未找到评论" in text_out:
            logger.warning(f"MCP Tool call '{tool_name}' returned error: {text_out}")
            return {"success": False, "raw_text": text_out, "result": res_payload, "error": text_out}

        return {"success": True, "raw_text": text_out, "result": res_payload}

    def check_login_status(self) -> bool:
        res = self.call_tool("check_login_status", {})
        if res.get("success") and "已登录" in res.get("raw_text", ""):
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
        小红书视频发布（支持私密/公开模式，目前默认私密）
        """
        clean_title = title.replace("！", "").replace("!", "")[:20]
        visibility = self.account_config.get("visibility", "仅自己可见")
        
        res = self.call_tool("publish_with_video", {
            "title": clean_title,
            "content": content_text,
            "video": video_path,
            "tags": topics[:5],
            "visibility": visibility
        })

        if res.get("success") and "成功" in res.get("raw_text", ""):
            return {
                "success": True,
                "draft_id": f"xhs_{int(os.path.getmtime(video_path))}",
                "message": res.get("raw_text")
            }
        return {
            "success": False,
            "draft_id": None,
            "message": res.get("raw_text") or str(res.get("error"))
        }

    def get_mentions_and_comments(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        获取收到的评论和@通知
        """
        res = self.call_tool("list_notifications", {
            "tab": "mentions",
            "limit": limit
        })
        if not res.get("success"):
            return []
        
        try:
            data = json.loads(res.get("raw_text", "{}"))
            return data.get("items", [])
        except Exception as e:
            logger.error(f"Failed to parse mentions: {e}")
            return []

    def reply_comment(self, comment_id: str, content: str) -> bool:
        """
        使用 reply_notification 直接回复收到的评论
        """
        res = self.call_tool("reply_notification", {
            "comment_id": comment_id,
            "content": content
        })
        return res.get("success", False)

    def post_seed_comment(self, feed_id: str, xsec_token: str, content: str) -> bool:
        """
        在指定小红书笔记下发表冷启动第一条主评论（抛砖引玉）
        """
        res = self.call_tool("post_comment_to_feed", {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "content": content
        })
        return res.get("success", False)
