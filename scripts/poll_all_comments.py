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

    # 2. 运行小红书守护（根据多平台策略中心控制）
    try:
        from src.platform_policy import get_platform_policy
        xhs_policy = get_platform_policy("xiaohongshu")
        interaction_policy = xhs_policy.get("interaction_policy", {})

        if interaction_policy.get("enable_background_polling", False):
            from scripts.poll_xhs_comments import poll_and_reply_xhs_comments
            # 若策略为 manual_approval，则强制 dry_run=True（只产出建议话术与日志，不调用直接写入）
            is_dry_run = (interaction_policy.get("reply_mode") == "manual_approval")
            xhs_records = poll_and_reply_xhs_comments(dry_run=is_dry_run)
            if xhs_records:
                all_replied.extend(xhs_records)
        else:
            logger.info("Xiaohongshu polling is currently DISABLED by platform policy (safe/standby mode).")
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
                card_title += f"，含 {manual_count} 条待手动回复）"
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
            logger.info(f"Dispatched comment guard report to Feishu, msg_id={msg_id}")
        except Exception as e:
            logger.error(f"Failed to push comment report to Feishu: {e}")

if __name__ == "__main__":
    run()
