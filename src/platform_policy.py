"""
src/platform_policy.py
多平台策略配置中心内核模块
集中加载 config/platform_policies.yaml，为各模块提供平台策略解析
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any

PIPELINE_ROOT = Path(__file__).resolve().parent.parent

_POLICIES_CACHE: Dict[str, Any] = {}

def load_platform_policies(force_reload: bool = False) -> Dict[str, Any]:
    global _POLICIES_CACHE
    if _POLICIES_CACHE and not force_reload:
        return _POLICIES_CACHE

    config_path = PIPELINE_ROOT / "config" / "platform_policies.yaml"
    if not config_path.exists():
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _POLICIES_CACHE = data.get("platforms", {})
    return _POLICIES_CACHE

def get_platform_policy(platform: str) -> Dict[str, Any]:
    """
    根据平台标识获取对应策略，未匹配时回退到 default 策略
    """
    policies = load_platform_policies()
    p_key = (platform or "").lower().strip()
    if p_key in policies:
        return policies[p_key]
    # 支持别名映射
    if p_key in ("xhs", "xiaohongshu-mcp", "xhs-tech-main"):
        return policies.get("xiaohongshu", policies.get("default", {}))
    if p_key in ("dy", "douyin-tech-main", "douyin-news-fast"):
        return policies.get("douyin", policies.get("default", {}))
    return policies.get("default", {})

def get_footer_callout(platform: str = "xiaohongshu") -> str:
    """获取指定平台的视频画面底部引导文案"""
    policy = get_platform_policy(platform)
    text_policy = policy.get("text_policy", {})
    return text_policy.get("footer_callout", "💡 欢迎评论区交流")
