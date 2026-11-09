import os
import time
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
)

logger = logging.getLogger(__name__)

_ALERT_FILE = Path(
    os.getenv("AUTOCONTENT_ALERTS_FILE")
    or Path(__file__).resolve().parent.parent.parent / "data" / "alerts.jsonl"
)   # 可用 AUTOCONTENT_ALERTS_FILE 重定向（自检/CI 不污染生产告警流）


def _alert(kind: str, detail: Dict[str, Any]) -> None:
    """发送失败告警（T3.2-5）。

    飞书的坑有两个：`success()` 为真但 `code != 0`；以及 post 富文本的代码块标签
    在移动端不渲染却照样返回 success。统一在 code != 0 时落一条告警，避免静默失败。
    """
    payload = {"ts": datetime.now().isoformat(timespec="seconds"),
               "kind": kind, "source": "feishu.approval_service", **detail}
    try:
        _ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _ALERT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(f"告警落盘失败（{kind}）: {exc}")


def _ok(resp: Any, where: str) -> bool:
    """统一判定：飞书 SDK 的正确判据是 `code == 0`，不是 `success()`。"""
    code = getattr(resp, "code", -1)
    if code != 0:
        logger.error(f"[FEISHU] {where} 失败: code={code} msg={getattr(resp, 'msg', None)}")
        _alert("feishu_api_error", {"where": where, "code": code,
                                    "msg": getattr(resp, "msg", None)})
        return False
    return True


def _probe_duration_ms(path: str) -> Optional[int]:
    """取视频时长（毫秒）。media 消息用它显示时长；取不到就返回 None（不阻断发送）。"""
    try:
        import subprocess
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path], text=True, timeout=20,
        ).strip()
        return int(float(out) * 1000)
    except Exception:  # noqa: BLE001
        return None


def _file_sha256(path: str) -> Optional[str]:
    """算文件指纹（D1=A：调用方没给 content_hash 时由文件自身派生）。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        logger.warning(f"读取视频算指纹失败，将拒绝发送: {path} ({exc})")
        return None
    return h.hexdigest()

class FeishuApprovalService:
    """
    飞书卡片消息收发与更新服务
    """
    def __init__(self, app_id: Optional[str] = None, app_secret: Optional[str] = None):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET")
        if not self.app_id or not self.app_secret:
            raise ValueError("FEISHU_APP_ID or FEISHU_APP_SECRET is not configured!")
        
        self.client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()

    def send_approval_card(self, receive_id: str, receive_id_type: str, card_content: Dict[str, Any]) -> Optional[str]:
        """
        向指定用户或群聊发送交互式卡片
        :param receive_id: 用户 open_id 或群 chat_id
        :param receive_id_type: 'open_id' 或 'chat_id'
        :param card_content: 卡片 JSON 字典
        :return: message_id
        """
        req = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(json.dumps(card_content, ensure_ascii=False))
                .build()
            ).build()

        resp = self.client.im.v1.message.create(req)
        if not _ok(resp, "send_approval_card"):
            return None

        message_id = resp.data.message_id
        logger.info(f"Feishu card message sent successfully, message_id={message_id}")
        return message_id

    def update_card(self, message_id: str, card_content: Dict[str, Any], max_retries: int = 3) -> bool:
        """
        更新已发送卡片的内容与状态（带网络抖动重试机制，最多 3 次）
        """
        import time
        req = PatchMessageRequest.builder() \
            .message_id(message_id) \
            .request_body(
                PatchMessageRequestBody.builder()
                .content(json.dumps(card_content, ensure_ascii=False))
                .build()
            ).build()

        for attempt in range(1, max_retries + 1):
            try:
                resp = self.client.im.v1.message.patch(req)
                if getattr(resp, "code", -1) == 0:
                    return True
                logger.warning(f"Patch card message attempt {attempt}/{max_retries} failed: code={resp.code}, msg={resp.msg}")
            except Exception as e:
                logger.warning(f"Patch card message exception attempt {attempt}/{max_retries}: {e}")

            if attempt < max_retries:
                time.sleep(attempt * 1.0)

        logger.error(f"Failed to patch card message {message_id} after {max_retries} attempts.")
        return False

    def upload_and_send_video(
        self,
        receive_id: str,
        receive_id_type: str,
        video_path: str,
        file_name: Optional[str] = None,
        content_hash: Optional[str] = None,
        task_id: Optional[str] = None,
        cover_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        向飞书对话发送原生 mp4 视频原片（供人机审批预览）
        :param receive_id: 用户 open_id 或群 chat_id
        :param receive_id_type: 'open_id' 或 'chat_id'
        :param video_path: 视频文件本地绝对路径
        :param file_name: 上传文件名（显式指定时优先）
        :param content_hash: 内容指纹（T3.2：缺省时由视频文件自身 sha256 派生，D1=A）
        :param task_id: 任务 ID（缺省取视频所在目录名）
        :return: feishu message_id
        """
        if not os.path.exists(video_path):
            logger.error(f"Video file does not exist: {video_path}")
            return None

        # T3.2-1：文件名用 {task_id}_{content_hash[:8]}{ext}
        #   · 防缓存：内容变 → 指纹变 → 名字变（等价于原时间戳的作用）
        #   · 可辨识：同一成片永远同名，群里看到名字就知道是哪条片子的哪一版
        if not file_name:
            base_pure = os.path.basename(video_path)
            _, ext = os.path.splitext(base_pure)
            owner = task_id or os.path.basename(os.path.dirname(video_path)) or "task"
            chash = content_hash or _file_sha256(video_path)
            if not chash:
                # 拿不到指纹就不许发 —— 无指纹发送等于放弃去重与溯源能力
                logger.error(f"[FEISHU] 拒绝发送：无法取得内容指纹且未显式提供 content_hash ({video_path})")
                _alert("send_refused_no_fingerprint", {"video_path": video_path})
                return None
            fname = f"{owner}_{chash[:8]}{ext}"
        else:
            fname = file_name
        try:
            # 1. 以 mp4 上传（msg_type=media 要求 file_type=mp4；用 stream 会被当成普通文件）
            with open(video_path, "rb") as f:
                file_req = CreateFileRequest.builder().request_body(
                    CreateFileRequestBody.builder()
                    .file_type("mp4")
                    .file_name(fname)
                    .file(f)
                    .build()
                ).build()
                resp = self.client.im.v1.file.create(file_req)
                if not _ok(resp, "upload_mp4") or not getattr(resp, "data", None):
                    return None
                file_key = resp.data.file_key

            # 2. 封面（可选）：带 image_key 的 media 消息在飞书里是"视频卡片"形态，
            #    不带则只有播放器无封面
            image_key = ""
            if cover_path and os.path.exists(cover_path):
                try:
                    with open(cover_path, "rb") as cf:
                        ireq = CreateImageRequest.builder().request_body(
                            CreateImageRequestBody.builder()
                            .image_type("message")
                            .image(cf)
                            .build()
                        ).build()
                        iresp = self.client.im.v1.image.create(ireq)
                    if _ok(iresp, "upload_cover") and getattr(iresp, "data", None):
                        image_key = iresp.data.image_key
                except Exception as e:  # noqa: BLE001 — 封面失败不阻断发送
                    logger.warning(f"[FEISHU] 封面上传失败，改为无封面发送: {e}")

            # 3. 时长（毫秒）：media 消息需要，缺失时飞书仍可发送但不显示时长
            duration_ms = _probe_duration_ms(video_path)

            content: Dict[str, Any] = {"file_key": file_key}
            if image_key:
                content["image_key"] = image_key
            if duration_ms:
                content["duration"] = duration_ms

            media_msg_req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("media")
                    .content(json.dumps(content))
                    .build()
                ).build()
            f_resp = self.client.im.v1.message.create(media_msg_req)
            if _ok(f_resp, "send_media_message") and getattr(f_resp, "data", None):
                feishu_msg_id = f_resp.data.message_id
                logger.info(
                    f"Video media sent to Feishu, feishu_msg_id={feishu_msg_id}, "
                    f"file_name={fname}, image_key={bool(image_key)}, duration_ms={duration_ms}"
                )
                return feishu_msg_id
            return None
        except Exception as e:
            logger.exception(f"Error in upload_and_send_video: {e}")
            return None

    def send_text_message(self, receive_id: str, receive_id_type: str, text: str) -> Optional[str]:
        """
        向指定用户或群聊发送普通文本通知消息
        :param receive_id: 用户 open_id 或群 chat_id
        :param receive_id_type: 'open_id' 或 'chat_id'
        :param text: 文本内容
        :return: message_id
        """
        try:
            req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}, ensure_ascii=False))
                    .build()
                ).build()
            resp = self.client.im.v1.message.create(req)
            if not _ok(resp, "send_text_message"):
                return None
            msg_id = resp.data.message_id
            logger.info(f"Feishu text message sent successfully, message_id={msg_id}")
            return msg_id
        except Exception as e:
            logger.exception(f"Error in send_text_message: {e}")
            return None
