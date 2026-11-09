import os
import sys
import logging
from pathlib import Path
from typing import List, Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AllCommentGuard")

class BaseCommentRunner:
    """平台巡检独立运行器基类"""
    def __init__(self, platform_name: str):
        self.platform_name = platform_name

    def run(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

class DouyinRunner(BaseCommentRunner):
    def __init__(self):
        super().__init__("抖音")

    def run(self) -> List[Dict[str, Any]]:
        from scripts.poll_douyin_comments import poll_and_reply_live_comments
        return poll_and_reply_live_comments() or []

class BilibiliRunner(BaseCommentRunner):
    def __init__(self):
        super().__init__("哔哩哔哩")

    def run(self) -> List[Dict[str, Any]]:
        from scripts.poll_bilibili_comments import poll_and_reply_bilibili_comments
        return poll_and_reply_bilibili_comments(dry_run=False) or []

class ChannelsRunner(BaseCommentRunner):
    def __init__(self):
        super().__init__("微信视频号")

    def run(self) -> List[Dict[str, Any]]:
        from src.platform_policy import get_platform_policy
        channels_policy = get_platform_policy("channels")
        ch_interaction = channels_policy.get("interaction_policy", {})

        if ch_interaction.get("enable_background_polling", False):
            from scripts.poll_channels_comments import poll_and_reply_channels_comments
            # 方案 A 严格以 dry_run=True (建议拟写待办模式) 运行
            return poll_and_reply_channels_comments(dry_run=True) or []
        else:
            logger.info("WeChat Channels polling is currently DISABLED by platform policy.")
            return []

def run():
    logger.info("=== Starting Decoupled Comment Guard Cycle (All Platforms) ===")
    
    runners: List[BaseCommentRunner] = [
        DouyinRunner(),
        BilibiliRunner(),
        ChannelsRunner()
    ]

    all_replied = []
    platform_stats = {}

    # 核心解耦循环：单平台故障绝对隔离，绝不波及后续平台
    for runner in runners:
        p_name = runner.platform_name
        logger.info(f"--- Running isolated runner: {p_name} ---")
        try:
            records = runner.run()
            count = len(records)
            all_replied.extend(records)
            platform_stats[p_name] = {"status": "SUCCESS", "count": count}
            logger.info(f"Runner [{p_name}] finished successfully. Handled: {count}")
        except Exception as e:
            logger.error(f"Runner [{p_name}] encountered isolated error: {e}", exc_info=True)
            platform_stats[p_name] = {"status": "ERROR", "error": str(e), "count": 0}

    logger.info(f"=== Comment Guard Cycle Finished. Summary: {platform_stats} | Total Replied: {len(all_replied)} ===")

    # 若有互动产生，向飞书会话推送统一卡片
    if all_replied:
        try:
            from src.feishu.approval_service import FeishuApprovalService
            service = FeishuApprovalService()
            target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
            rec_type = "open_id" if target.startswith("ou_") else "chat_id"

            elements = []
            manual_count = 0
            for r in all_replied:
                is_manual = (r.get("reply_mode") == "manual_approval" or r.get("platform") == "xiaohongshu")
                if is_manual:
                    manual_count += 1
                    reply_block = f"💡 **AI 建议回复（可长按直接复制在手机App回复）**：\n```text\n{r['reply']}\n```"
                else:
                    reply_block = f"🤖 **已自动直发回复**: {r['reply']}"

                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"🏷️ **平台**: {r['platform']}  |  **作品**: 《{r['feed_title']}》\n👤 **粉丝**: `{r['user']}`\n💬 **评论**: {r['comment']}\n{reply_block}"
                    }
                })
                elements.append({"tag": "hr"})

            card_title = f"💬 评论区互动报告（新增 {len(all_replied)} 条"
            if manual_count > 0:
                card_title += f"，含 {manual_count} 条待手机端复制）"
            else:
                card_title += "）"

            card_content = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": card_title},
                    "template": "blue"
                },
                "elements": elements
            }
            msg_id = service.send_approval_card(receive_id=target, receive_id_type=rec_type, card_content=card_content)
            logger.info(f"Dispatched decoupled comment guard report to Feishu, msg_id={msg_id}")
        except Exception as e:
            logger.error(f"Failed to push comment report to Feishu: {e}")

if __name__ == "__main__":
    run()
