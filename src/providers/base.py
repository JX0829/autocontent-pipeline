from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseSocialProvider(ABC):
    """
    自媒体平台发布/草稿 Provider 基类
    """
    def __init__(self, account_config: Dict[str, Any]):
        self.account_config = account_config
        self.account_id = account_config.get("id", "default")
        self.platform = account_config.get("platform", "unknown")

    @abstractmethod
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
        上传视频并存为草稿
        :return: {"success": bool, "draft_id": Optional[str], "message": str}
        """
        pass

    @abstractmethod
    def check_login_status(self) -> bool:
        """
        检查账号登录态是否有效
        """
        pass
