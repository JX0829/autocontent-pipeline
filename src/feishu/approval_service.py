import os
import json
import logging
from typing import Dict, Any, Optional
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    PatchMessageRequest,
    PatchMessageRequestBody,
    CreateFileRequest,
    CreateFileRequestBody
)

logger = logging.getLogger(__name__)

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
        if not resp.success():
            logger.error(f"Failed to send feishu card: code={resp.code}, msg={resp.msg}, log_id={resp.get_log_id()}")
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
                if resp.success():
                    return True
                logger.warning(f"Patch card message attempt {attempt}/{max_retries} failed: code={resp.code}, msg={resp.msg}")
            except Exception as e:
                logger.warning(f"Patch card message exception attempt {attempt}/{max_retries}: {e}")

            if attempt < max_retries:
                time.sleep(attempt * 1.0)

        logger.error(f"Failed to patch card message {message_id} after {max_retries} attempts.")
        return False

    def upload_and_send_video(self, receive_id: str, receive_id_type: str, video_path: str, file_name: Optional[str] = None) -> Optional[str]:
        """
        向飞书对话发送原生 mp4 视频原片（供人机审批预览）
        :param receive_id: 用户 open_id 或群 chat_id
        :param receive_id_type: 'open_id' 或 'chat_id'
        :param video_path: 视频文件本地绝对路径
        :param file_name: 上传文件名
        :return: message_id
        """
        if not os.path.exists(video_path):
            logger.error(f"Video file does not exist: {video_path}")
            return None

        fname = file_name or os.path.basename(video_path)
        try:
            with open(video_path, "rb") as f:
                file_req = CreateFileRequest.builder().request_body(
                    CreateFileRequestBody.builder()
                    .file_type("mp4")
                    .file_name(fname)
                    .file(f)
                    .build()
                ).build()
                resp = self.client.im.v1.file.create(file_req)
                if not resp.success():
                    logger.error(f"Failed to upload video to Feishu: code={resp.code}, msg={resp.msg}")
                    return None
                file_key = resp.data.file_key

            msg_req = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("media")
                    .content(json.dumps({"file_key": file_key}))
                    .build()
                ).build()
            m_resp = self.client.im.v1.message.create(msg_req)
            if not m_resp.success():
                logger.error(f"Failed to send video media message: code={m_resp.code}, msg={m_resp.msg}")
                return None
            video_msg_id = m_resp.data.message_id
            logger.info(f"Video media sent to Feishu successfully, message_id={video_msg_id}")
            return video_msg_id
        except Exception as e:
            logger.exception(f"Error in upload_and_send_video: {e}")
            return None
