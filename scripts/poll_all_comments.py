import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AllCommentGuard")

def run():
    logger.info("=== Starting Unified Comment Guard Cycle (Douyin + Xiaohongshu) ===")
    
    all_replied = []

    # 1. 运行抖音守护
    try:
        from scripts.poll_douyin_comments import poll_and_reply_live_comments
        dy_records = poll_and_reply_live_comments()
        if dy_records:
            all_replied.extend(dy_records)
    except Exception as e:
        logger.error(f"Douyin comment guard failed: {e}")

    # 2. 运行小红书守护
    try:
        from scripts.poll_xhs_comments import poll_and_reply_xhs_comments
        xhs_records = poll_and_reply_xhs_comments(dry_run=False)
        if xhs_records:
            all_replied.extend(xhs_records)
    except Exception as e:
        logger.error(f"Xiaohongshu comment guard failed: {e}")

    logger.info(f"=== Unified Comment Guard Cycle Completed. Total replied: {len(all_replied)} ===")

    # 如果有新产生的回复，自动向飞书推送互动报告卡片
    if all_replied:
        try:
            import os
            from src.feishu.approval_service import FeishuApprovalService
            service = FeishuApprovalService()
            target = os.getenv("FEISHU_APPROVAL_CHAT_ID", "ou_bc831d683c9b59369bc86ee1561ed226")
            rec_type = "open_id" if target.startswith("ou_") else "chat_id"

            elements = []
            for r in all_replied:
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"🏷️ **平台**: {r['platform']}  |  **作品**: 《{r['feed_title']}》\n👤 **粉丝**: `{r['user']}`\n💬 **评论**: {r['comment']}\n🤖 **已自动回复**: {r['reply']}"
                    }
                })
                elements.append({"tag": "hr"})

            card_content = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": f"💬 评论区自动互动报告（本次新增 {len(all_replied)} 条）"},
                    "template": "blue"
                },
                "elements": elements
            }
            msg_id = service.send_approval_card(receive_id=target, receive_id_type=rec_type, card_content=card_content)
            logger.info(f"Dispatched comment guard report to Feishu, msg_id={msg_id}")
        except Exception as e:
            logger.error(f"Failed to push comment report to Feishu: {e}")

if __name__ == "__main__":
    run()
