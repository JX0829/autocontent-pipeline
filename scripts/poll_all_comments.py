import sys
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AllCommentGuard")

def run():
    logger.info("=== Starting Unified Comment Guard Cycle (Douyin + Xiaohongshu) ===")
    
    # 1. 运行抖音守护
    try:
        from scripts.poll_douyin_comments import poll_and_reply_live_comments
        poll_and_reply_live_comments()
    except Exception as e:
        logger.error(f"Douyin comment guard failed: {e}")

    # 2. 运行小红书守护
    try:
        from scripts.poll_xhs_comments import poll_and_reply_xhs_comments
        poll_and_reply_xhs_comments(dry_run=False)
    except Exception as e:
        logger.error(f"Xiaohongshu comment guard failed: {e}")

    logger.info("=== Unified Comment Guard Cycle Completed ===")

if __name__ == "__main__":
    run()
