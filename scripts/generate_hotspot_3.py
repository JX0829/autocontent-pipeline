import asyncio
import os
import sys
import json
import logging
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path('/Users/a1/Downloads/workspace/team/project/autocontent-pipeline')
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.auto_generate_from_digest import trigger_video_from_digest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Hotspot3Runner")

NEWS_CONTENT = """【移动通信协议高危警报：CVE 披露 SMS of Death 漏洞，零点击静默导致手机 Telephony 进程异常锁死】
- 核心事实：安全研究团队公开曝光针对移动通信基带与短信栈的零点击（Zero-click）远程 DoS 漏洞。攻击者通过构造畸形 WAP Push 与底层 PDU 二进制数据包，无需接收方任何点击交互即可触发 Telephony 核心服务内存解包死循环。
- 危害影响：可瞬间导致设备基带掉网、电话栈崩溃与无限软重启，团队已向主流芯片厂商与开源底层同步提交热修复补丁。
- 极客思考：当未受信任的空中无线电数据包能够直接穿透基带进入操作系统用户态进程，底层通信与现代移动操作系统的安全边界该如何重新构筑？
"""

async def main():
    logger.info("==================== 开始制作【热点3·零点击短信漏洞】短视频 ====================")
    res = await trigger_video_from_digest(news_content=NEWS_CONTENT)
    if res:
        task_id = res["task_id"]
        logger.info(f"热点3短视频制作成功: {res['title']} (Task ID: {task_id})")
        db_path = PROJECT_ROOT / "data" / "pipeline_state.db"
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("UPDATE content_tasks SET status = 'WAITING_APPROVAL', updated_at = CURRENT_TIMESTAMP WHERE task_id = ?", (task_id,))
            conn.commit()
            conn.close()
            logger.info(f"Task {task_id} status updated to WAITING_APPROVAL")
        except Exception as e:
            logger.warning(f"Failed to update task {task_id} status: {e}")
        print("RESULT_SUCCESS:" + json.dumps(res, ensure_ascii=False))
    else:
        logger.error("热点3短视频制作失败！")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
