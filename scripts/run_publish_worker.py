#!/usr/bin/env python3
"""
scripts/run_publish_worker.py
Phase 3: 草稿箱上传与发布 Worker
轮询或手动接力 SQLite 中状态为 APPROVED 的任务，
调度对应的 Provider (Douyin / Xiaohongshu) 自动化上传视频、填写文案与话题标签，
并保存至平台草稿箱（严格遵循草稿箱模式，绝不直发）。
上传完成后将状态更新为 DRAFTED，记录 platform_draft_id。
"""

import os
import sys
import json
import hashlib
import logging
import argparse
from typing import Optional, Dict, Any
import yaml
from pathlib import Path
from dotenv import load_dotenv

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

load_dotenv(PIPELINE_ROOT / ".env")

from src.db import PipelineDB
from src.providers.douyin import DouyinProvider
from src.providers.xiaohongshu import XiaohongshuProvider
from src.providers.bilibili import BilibiliProvider
from src.providers.channels_cdp import ChannelsCDPProvider
from src.feishu.approval_service import FeishuApprovalService
from src.feishu.card_builder import FeishuCardBuilder, MissingChatIdError
from src.utils.normalize import content_hash as compute_content_hash
from src.utils.text_sanitizer import ensure_clean_short_title

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("PublishWorker")

def video_sha256(path: str) -> str:
    """视频文件指纹（供台账 dedupe_key 使用；读不到返回空串）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        logger.warning(f"读取视频算指纹失败（台账 key 会退化）: {path} ({exc})")
        return ""
    return h.hexdigest()


def send_dedupe_key(content_hash: str, video_sha: str, account: str) -> str:
    """与 `scripts/hooks/send_ledger.py` **完全一致**的 dedupe_key 口径。

    两处必须同口径，否则 hook 层拦下的和 worker 层拦下的永远对不上。
    """
    return hashlib.sha256(f"{content_hash}|{video_sha}|{account}".encode("utf-8")).hexdigest()


def claim_channel(db: PipelineDB, task_id: str, channel: str, content_hash: str,
                  vhash: str, video_path: str, chat_id: str = "") -> Optional[str]:
    """发送前抢占台账槽位（T3.2-2 / D2）。

    返回 dedupe_key 表示抢到；返回 None 表示「这条内容在这个渠道已经发过了」。
    """
    key = send_dedupe_key(content_hash, vhash, channel)
    if db.claim_send_slot(key, task_id, channel, chat_id, video_path):
        return key
    return None


def notify_feishu(message: str, card: Optional[dict] = None, *, db: Optional[PipelineDB] = None,
                  dedupe_key: str = "") -> Optional[str]:
    """向飞书审批会话播报（统一 Card 2.0）。

    T3.2-R2：会话 ID 必须来自 `FEISHU_APPROVAL_CHAT_ID`，缺失即 `MissingChatIdError`，
    严禁代码里写死真实 open_id 兜底。
    """
    target = os.getenv("FEISHU_APPROVAL_CHAT_ID")
    if not target:
        raise MissingChatIdError(
            "FEISHU_APPROVAL_CHAT_ID 未配置：拒绝发送（不允许硬编码会话 ID 兜底）")

    rec_type = "open_id" if target.startswith("ou_") else "chat_id"
    svc = FeishuApprovalService()

    if card is None:
        msg_id = svc.send_text_message(receive_id=target, receive_id_type=rec_type, text=message)
    else:
        import lark_oapi as lark  # noqa: F401  (SDK 侧初始化由 svc 完成)
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        req = CreateMessageRequest.builder() \
            .receive_id_type(rec_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(target)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            ).build()
        resp = svc.client.im.v1.message.create(req)
        code = getattr(resp, "code", -1)
        if code != 0:
            logger.error(f"Feishu card2 send failed: code={code} msg={getattr(resp,'msg',None)}")
            try:
                with (PIPELINE_ROOT / "data" / "alerts.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"kind": "feishu_api_error", "where": "publish_worker.card2",
                                        "code": code, "msg": getattr(resp, "msg", None)},
                                       ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning(f"告警落盘失败: {exc}")
            return None
        msg_id = resp.data.message_id

    # 发送成功之后才写台账（QA#10：seen / 已发送状态只在外部确认成功后写）
    if msg_id and db is not None and dedupe_key:
        try:
            db.mark_sent(dedupe_key, msg_id)
        except Exception as exc:  # noqa: BLE001 - 台账回写失败必须可见
            logger.error(f"回写 send_ledger.feishu_msg_id 失败: {exc}")
    return msg_id


def load_accounts_map():
    config_path = PIPELINE_ROOT / "config" / "accounts.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {acc["id"]: acc for acc in data.get("accounts", [])}


def process_task(task_id: str, db: PipelineDB, accounts_map: dict):
    task = db.get_task(task_id)
    if not task:
        logger.error(f"Task '{task_id}' not found in SQLite DB.")
        return False

    status = task.get("status")
    if status not in ("APPROVED", "WAITING_APPROVAL"):
        logger.warning(f"Task '{task_id}' is in status '{status}', expected 'APPROVED' or 'WAITING_APPROVAL'.")
        return False

    draft_script = json.loads(task.get("draft_script_json") or "{}")
    video_path = task.get("video_path")
    cover_path = task.get("cover_path")
    title = draft_script.get("title", "")
    topics = draft_script.get("topics") or draft_script.get("tags") or []
    content_text = draft_script.get("content_text") or draft_script.get("fact_summary_150") or ""

    import re
    def sanitize_text(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'https?://[^\s]+', '', text)
        text = re.sub(r'github\.com/[^\s]+', '', text)
        return text.strip()

    title = sanitize_text(title)
    content_text = sanitize_text(content_text)

    # T3.2-3：发送前必须有内容指纹（无指纹 = 放弃去重与溯源能力）
    c_hash = compute_content_hash(title, content_text)
    if not c_hash:
        logger.error(f"Task '{task_id}' 无法计算 content_hash，拒绝发送")
        return False
    vhash = video_sha256(video_path or "")
    logger.info(f"Task '{task_id}' content_hash={c_hash[:12]}… video_sha={vhash[:12] or '-'}")

    dispatch_results = []

    # 1. B站分发（若启用 bili-tech-main）
    bili_account = accounts_map.get("bili-tech-main")
    if bili_account and bili_account.get("enabled", True):
        bili_video = str(task.get("video_landscape_path") or video_path or "")
        bili_cover = str(task.get("cover_landscape_path") or cover_path or "")
        key = claim_channel(db, task_id, "bilibili", c_hash, vhash, bili_video)
        if not key:
            logger.warning(f"Task '{task_id}' 已在 B站 发送过（台账拦截）")
            dispatch_results.append(f"📺 **B站**: ⏭️ 台账命中，已发送过（未重复上传）")
        else:
            logger.info(f"Dispatching task '{task_id}' to Bilibili ({bili_account.get('publish_mode', 'draft')})...")
            try:
                bili_provider = BilibiliProvider(bili_account)
                bili_res = bili_provider.save_draft(
                    video_path=bili_video, cover_path=bili_cover, title=title,
                    topics=topics, content_text=content_text, declare_aigc=True
                )
                if bili_res.get("success"):
                    draft_id = bili_res.get("draft_id")
                    dispatch_results.append(f"📺 **B站 (16:9横版+置顶评论)**: ✅ 上传成功 (`{draft_id}`)")
                    try:
                        aid = bili_res.get("aid")
                        if aid:
                            from src.providers.bilibili_comment import BilibiliCommentClient
                            b_client = BilibiliCommentClient()
                            seed_msg = f"📌 项目开源地址与启动命令已整理在视频简介！\\n{content_text}\\n欢迎在弹幕与评论区交流探讨～"
                            b_client.post_and_top_comment(oid=aid, message=seed_msg)
                    except Exception as ce:
                        logger.warning(f"Failed to post top comment to Bilibili: {ce}")
                        dispatch_results.append(f"📺 **B站置顶评论**: ⚠️ {ce}")
                else:
                    dispatch_results.append(f"📺 **B站**: ⚠️ {bili_res.get('message')}")
            except Exception as e:
                dispatch_results.append(f"📺 **B站**: ❌ 异常 ({e})")

    # 2. 微信视频号分发（若启用 channels-tech-main）
    channels_account = accounts_map.get("channels-tech-main")
    if channels_account and channels_account.get("enabled", True):
        key = claim_channel(db, task_id, "channels", c_hash, vhash, video_path or "")
        if not key:
            logger.warning(f"Task '{task_id}' 已在 视频号 发送过（台账拦截）")
            dispatch_results.append(f"📲 **微信视频号**: ⏭️ 台账命中，已发送过")
        else:
            logger.info(f"Dispatching task '{task_id}' to WeChat Channels ({channels_account.get('publish_mode', 'public')})...")
            try:
                channels_provider = ChannelsCDPProvider(channels_account)
                channels_res = channels_provider.save_draft(
                    video_path=video_path, cover_path=cover_path, title=title,
                    topics=topics, content_text=content_text, declare_aigc=False
                )
                if channels_res.get("success"):
                    dispatch_results.append(f"📲 **微信视频号 (手机原生发布素材)**: ✅ 9:16 原片与纯净文案已备齐")
                else:
                    dispatch_results.append(f"📲 **微信视频号**: ⚠️ {channels_res.get('message')}")
            except Exception as e:
                dispatch_results.append(f"📲 **微信视频号**: ❌ 异常 ({e})")

    # 3. 抖音分发（账号按平台从 accounts.yaml 解析，默认发布为仅自己可见）
    douyin_account = next((a for a in accounts_map.values()
                           if str(a.get("platform") or "") == "douyin" and a.get("enabled", True)), None)
    if douyin_account and douyin_account.get("enabled", True):
        key = claim_channel(db, task_id, "douyin", c_hash, vhash, video_path or "")
        if not key:
            logger.warning(f"Task '{task_id}' 已在 抖音 发送过（台账拦截）")
            dispatch_results.append(f"🎵 **抖音**: ⏭️ 台账命中，已发送过")
        else:
            logger.info(f"Dispatching task '{task_id}' to Douyin ({douyin_account.get('publish_mode', 'private_publish')})...")
            try:
                dy_provider = DouyinProvider(douyin_account)
                dy_res = dy_provider.save_draft(
                    video_path=video_path or "", cover_path=cover_path or "", title=title,
                    topics=topics, content_text=content_text,
                    declare_aigc=douyin_account.get("declare_aigc", True)
                )
                if dy_res.get("success"):
                    draft_id = dy_res.get("draft_id", "private_ok")
                    dispatch_results.append(f"🎵 **抖音 (私密作品·仅自己可见)**: ✅ 视频已直传并保存 (ID: {draft_id})")
                else:
                    dispatch_results.append(f"🎵 **抖音**: ⚠️ {dy_res.get('message')}")
            except Exception as e:
                dispatch_results.append(f"🎵 **抖音**: ❌ 异常 ({e})")

    # 4. 手机发帖专例文案推送（小红书 7 天避险冷冻期 & 手机发布）
    #    T3.2-R3：卡片由 card_builder 直接产出 Card 2.0，不再经过 post 富文本中间结构
    clean_tags = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])
    short_title = ensure_clean_short_title(title, max_chars=30)
    full_body = f"{content_text}\n\n💡 欢迎在评论区交流讨论～\n\n{clean_tags}"

    try:
        key = claim_channel(db, task_id, "feishu_material", c_hash, vhash, video_path or "",
                            chat_id=os.getenv("FEISHU_APPROVAL_CHAT_ID") or "")
        if not key:
            logger.warning(f"Task '{task_id}' 手机素材已推送过（台账拦截）")
            dispatch_results.append(f"📕 **小红书 (手机发帖素材)**: ⏭️ 台账命中，已推送过")
        else:
            card = FeishuCardBuilder.build_mobile_copy_card(
                task_id=task_id, short_title=short_title, full_body=full_body)
            msg_id = notify_feishu("", card=card, db=db, dedupe_key=key)
            if msg_id:
                dispatch_results.append(f"📕 **小红书 (手机发帖素材)**: ✅ 已推送（feishu_msg_id=`{msg_id}`）")
            else:
                dispatch_results.append(f"📕 **小红书**: ❌ 卡片发送失败（详见告警）")
    except MissingChatIdError as e:
        logger.error(f"Task '{task_id}' 素材推送中止: {e}")
        dispatch_results.append(f"📕 **小红书**: ❌ {e}")
    except Exception as e:
        dispatch_results.append(f"📕 **小红书**: ❌ 素材推送失败 ({e})")

    # 5. 更新 SQLite 数据库任务状态
    db.update_task_status(task_id, "DISPATCHED", platform_draft_id=f"matrix_{task_id}")
    logger.info(f"Task '{task_id}' successfully transitioned to DISPATCHED")

    # 6. 向飞书播报整体分发汇总卡片（汇总卡不参与台账，属于汇报而非投递内容）
    summary_msg = f"🎉 **任务 `{task_id}` 动态多平台分发执行完毕**:\n\n" + "\n".join(dispatch_results)
    try:
        summary_card = FeishuCardBuilder.build_mobile_copy_card(
            task_id=task_id, short_title=short_title, full_body=full_body,
            heading=f"🎉 任务 {task_id} 发布完成汇报 & 📱 手机端纯净素材",
            lead=summary_msg + "\n\n───────────────")
        notify_feishu("", card=summary_card)
    except MissingChatIdError as e:
        logger.error(f"汇总播报跳过: {e}")
    return True


def main():
    parser = argparse.ArgumentParser(description="草稿箱上传与发布 Worker")
    parser.add_argument("--task-id", help="指定要处理的 APPROVED 任务 ID")
    parser.add_argument("--poll", action="store_true", help="以轮询模式持续监听 APPROVED 任务")
    args = parser.parse_args()

    db_path = os.getenv("SQLITE_DB_PATH", str(PIPELINE_ROOT / "data/pipeline_state.db"))
    db = PipelineDB(db_path)
    accounts_map = load_accounts_map()

    if args.task_id:
        process_task(args.task_id, db, accounts_map)
    else:
        logger.info("Checking for any APPROVED tasks in DB...")
        with db.get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT task_id FROM content_tasks WHERE status = 'APPROVED' ORDER BY updated_at ASC")
            rows = cur.fetchall()

        if not rows:
            logger.info("No tasks currently in APPROVED status.")
        else:
            logger.info(f"Found {len(rows)} APPROVED task(s). Processing...")
            for row in rows:
                process_task(row[0], db, accounts_map)


if __name__ == "__main__":
    main()
