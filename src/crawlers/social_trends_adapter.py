"""
src/crawlers/social_trends_adapter.py
全网社交热点统一适配器
双轨并行聚合：
1. MediaCrawler: 小红书(xhs) / 微博(wb) / B站(bili) 科技关键词搜索
2. X (Twitter): 本地 twitter-cli 工具只读搜索（支持 5s 优雅熔断降级）
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("SocialTrendsAdapter")

PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
TWITTER_CLI_BIN = Path.home() / ".local" / "bin" / "twitter"

def fetch_x_trending(query: str = "AI OR LLM OR OpenSource", max_count: int = 5, timeout_sec: int = 6) -> List[Dict[str, Any]]:
    """
    通过本机 twitter-cli 只读检索 X (Twitter) 热门推文
    设置严格超时与未登录降级，防止阻塞速递主流程
    """
    if not TWITTER_CLI_BIN.exists():
        return []

    cmd = [
        str(TWITTER_CLI_BIN),
        "-c",  # 紧凑模式
        "search",
        query,
        f"--count={max_count}"
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec
        )
        if res.returncode != 0:
            logger.info(f"X search exited with status {res.returncode} (auth pending or network limit)")
            return []

        # 尝试逐行或按段解析推文内容
        output = res.stdout.strip()
        if not output:
            return []

        items = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("---") or "Getting Twitter cookies" in line:
                continue
            # 基础文本提纯
            items.append({
                "title": line[:80],
                "desc": line,
                "url": "https://x.com",
                "platform": "twitter",
                "author": "X_Tech"
            })
            if len(items) >= max_count:
                break
        return items
    except subprocess.TimeoutExpired:
        logger.info(f"X search timed out after {timeout_sec}s, gracefully fallback.")
        return []
    except Exception as e:
        logger.warning(f"X search encountered error: {e}")
        return []

def fetch_social_trends(
    xhs_keywords: str = "AI开源,大模型",
    x_query: str = "AI OR Agent OR OpenSource",
    max_per_platform: int = 3
) -> Dict[str, List[Dict[str, Any]]]:
    """
    统一获取多平台社交热点
    :return: {"xhs": [...], "twitter": [...]}
    """
    result: Dict[str, List[Dict[str, Any]]] = {
        "xhs": [],
        "twitter": []
    }

    # 1. 抓取 MediaCrawler 小红书/国内热点
    try:
        from src.crawlers.mediacrawler_adapter import search_media_trending, is_mediacrawler_available
        if is_mediacrawler_available():
            # 遵守避险策略，轻量只读搜索（设置 8s 极简熔断）
            notes = search_media_trending(
                platform="xhs",
                keywords=xhs_keywords,
                max_count=max_per_platform,
                timeout_sec=8
            )
            result["xhs"] = notes
    except Exception as e:
        logger.warning(f"MediaCrawler fetch failed: {e}")

    # 2. 抓取 X (Twitter) 推文
    try:
        tweets = fetch_x_trending(query=x_query, max_count=max_per_platform, timeout_sec=5)
        result["twitter"] = tweets
    except Exception as e:
        logger.warning(f"Twitter fetch failed: {e}")

    return result
