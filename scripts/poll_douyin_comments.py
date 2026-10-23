import os
import sys
import time
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import PipelineDB
from src.safety.comment_funnel import CommentSafetyFunnel
from src.providers.douyin import DouyinProvider
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DouyinCommentWorker")

LOG_FILE = PROJECT_ROOT / "data" / "comment_replies_log.jsonl"

def append_audit_log(record: dict):
    os.makedirs(LOG_FILE.parent, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

def poll_and_reply_live_comments():
    logger.info("Starting live Douyin comment poll and guard cycle...")
    db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
    funnel = CommentSafetyFunnel()

    with open(PROJECT_ROOT / "config" / "accounts.yaml") as f:
        cfg = yaml.safe_load(f)
    acc = [a for a in cfg["accounts"] if a["id"] == "douyin-tech-main"][0]
    provider = DouyinProvider(acc)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context, browser = provider._get_browser_context(p, headless=True)
        page = context.new_page()

        try:
            page.goto("https://creator.douyin.com/creator-micro/interactive/comment", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # 点击刷新拉取最新
            refresh_btn = page.locator("text=点击刷新").first
            if refresh_btn.count() > 0:
                refresh_btn.click()
                time.sleep(3)
        except Exception as e:
            logger.error(f"Navigation error: {e}")
            browser.close()
            return

        # 查找所有包含评论容器的 class (container-sXKyMs)
        comment_containers = page.locator('div[class*="container-"]').all()
        extracted_comments = []

        for c_el in comment_containers:
            try:
                txt = c_el.inner_text().strip()
                # 必须包含操作按钮如'回复'且有实质内容
                if "回复" in txt and ("刚刚" in txt or "前" in txt or "202" in txt):
                    lines = [l.strip() for l in txt.split("\n") if l.strip()]
                    # 结构通常为: 0: 昵称, 1: 时间, 2: 评论文本, 3: 点赞数, 4: 回复
                    if len(lines) >= 3:
                        user_name = lines[0]
                        comment_text = lines[2]
                        # 排除非用户正文
                        if comment_text not in ["回复", "删除", "举报", "置顶", "全部评论"]:
                            reply_btn = c_el.locator('text="回复"').first
                            extracted_comments.append({
                                "user": user_name,
                                "text": comment_text,
                                "reply_btn": reply_btn,
                                "container": c_el
                            })
            except Exception:
                pass

        logger.info(f"Detected {len(extracted_comments)} live user comment(s)")

        # 默认科技类事实与暗号 (后续可从 DB 按 published_video_id 动态关联)
        fact_summary = "谷歌开源分布式LLM推理引擎llm-d 1.0稳定版，深度整合vLLM与SGLang，针对大规模Kubernetes与GPU集群做极致吞吐与KV Cache优化，高并发下首字延迟(TTFT)降低超80%。"
        keyword = "llmd"
        hook = "开源地址与一键部署包已整理在主页粉丝群，进群暗号【{keyword}】自取～"

        processed_count = 0
        for item in extracted_comments:
            comment_text = item["text"]
            user_name = item["user"]

            # 查重：避免重复回复
            with db.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM comment_interactions WHERE raw_comment = ?", (comment_text,))
                if cur.fetchone():
                    logger.info(f"Comment already processed in DB: '{comment_text}' by {user_name}")
                    continue

            logger.info(f"Processing new live comment: '{comment_text}' by {user_name}")
            res = funnel.process_comment(comment_text, fact_summary, keyword, hook)
            logger.info(f"Funnel verdict: Classification={res['classification']}, Reply='{res['reply']}'")

            reply_content = res.get("reply")
            reply_success = False

            if reply_content:
                try:
                    r_btn = item["reply_btn"]
                    r_btn.click()
                    time.sleep(1)

                    active_input = page.locator("div[contenteditable='true']").last
                    active_input.click()
                    active_input.press_sequentially(reply_content, delay=40)
                    time.sleep(1)

                    # 精确点击当前输入框下的发送按钮
                    send_candidates = page.locator("span:has-text('发送'), button:has-text('发送')").all()
                    for b in reversed(send_candidates):
                        if b.is_visible() and b.inner_text().strip() == "发送":
                            b.click()
                            time.sleep(3)
                            reply_success = True
                            break
                    logger.info(f"Replied to {user_name} on Douyin live platform: '{reply_content}'")
                except Exception as e:
                    logger.error(f"Failed to submit reply on DOM: {e}")

            # 状态持久化
            with db.get_conn() as conn:
                cur = conn.cursor()
                cur.execute("""
                INSERT OR REPLACE INTO comment_interactions
                (comment_id, task_id, published_video_id, raw_comment, sanitized_comment, classification, reply_content, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    f"c_{int(time.time())}_{processed_count}",
                    "task_google_llmd_001",
                    "7699504223221845284",
                    comment_text,
                    res["sanitized_comment"],
                    res["classification"],
                    reply_content,
                    "REPLIED" if reply_success else ("IGNORED" if not reply_content else "FAILED")
                ))
                conn.commit()

            append_audit_log({
                "timestamp": datetime.now().isoformat(),
                "user": user_name,
                "raw_comment": comment_text,
                "classification": res["classification"],
                "reply": reply_content,
                "status": "REPLIED" if reply_success else "SKIPPED_OR_IGNORED"
            })
            processed_count += 1
            time.sleep(2)

        browser.close()
        logger.info(f"Comment cycle finished. Handled {processed_count} new comment(s).")

if __name__ == "__main__":
    poll_and_reply_live_comments()
