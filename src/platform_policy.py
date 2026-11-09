# src/platform_policy.py
"""
多平台差异化策略管理模块
负责加载 config/platform_policies.yaml 并为视频生成、文案审查、草稿发布与评论互动提供策略支持
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "platform_policies.yaml"

_CACHED_POLICIES: Optional[Dict[str, Any]] = None
_LAST_MTIME: float = 0.0


def load_platform_policies(force_reload: bool = False) -> Dict[str, Any]:
    """
    加载平台策略配置（支持文件 mtime 变更自动重载）
    """
    global _CACHED_POLICIES, _LAST_MTIME
    if not CONFIG_PATH.exists():
        logger.warning(f"Platform policies config not found at {CONFIG_PATH}, returning fallback.")
        return {}

    try:
        current_mtime = CONFIG_PATH.stat().st_mtime
        if not force_reload and _CACHED_POLICIES is not None and current_mtime == _LAST_MTIME:
            return _CACHED_POLICIES

        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        _CACHED_POLICIES = data.get("platforms") or {}
        _LAST_MTIME = current_mtime
        logger.info(f"Loaded platform policies for: {list(_CACHED_POLICIES.keys())}")
        return _CACHED_POLICIES
    except Exception as e:
        logger.error(f"Error loading platform policies from {CONFIG_PATH}: {e}")
        return _CACHED_POLICIES if _CACHED_POLICIES is not None else {}


def get_platform_policy(platform: str) -> Dict[str, Any]:
    """
    获取指定平台的完整策略配置（若未定义则回退至 default）
    """
    policies = load_platform_policies()
    norm_platform = (platform or "").lower().strip()
    if norm_platform in ["douyin", "dy", "tiktok"]:
        key = "douyin"
    else:
        key = norm_platform

    policy = policies.get(key)
    if not policy:
        policy = policies.get("default", {})
    return policy


def get_footer_callout(platform: str = "douyin") -> str:
    """
    获取对应平台在短视频分镜与封面底部的引导语
    - 抖音：'💡 评论区AI回复，欢迎交流'
    """
    policy = get_platform_policy(platform)
    text_policy = policy.get("text_policy", {})
    return text_policy.get("footer_callout", "💡 欢迎评论区交流")


def get_seed_comment_hook(platform: str = "douyin") -> str:
    """
    获取对应平台在首评或置顶评论中的引流引导语
    """
    policy = get_platform_policy(platform)
    text_policy = policy.get("text_policy", {})
    return text_policy.get("seed_comment_hook", "开源项目直接在 GitHub 搜索对应项目名即可查看源码，欢迎评论区交流～")


def is_manual_approval_reply(platform: str) -> bool:
    """
    判断该平台的评论互动是否采用人工审批/建议推送模式（严禁脚本自动直发）
    """
    policy = get_platform_policy(platform)
    interaction_policy = policy.get("interaction_policy", {})
    reply_mode = interaction_policy.get("reply_mode", "manual_approval")
    return reply_mode == "manual_approval" or not interaction_policy.get("allow_direct_api_reply", False)


def get_max_title_chars(platform: str) -> int:
    """
    获取平台允许的最大标题字符数
    """
    policy = get_platform_policy(platform)
    return policy.get("text_policy", {}).get("max_title_chars", 20)
