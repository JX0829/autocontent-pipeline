"""
src/crawlers/agent_reach_adapter.py
Agent Reach 核心能力适配器 (v1.6.0 重构标准)
通过统一命令 `agent-reach`（免代理、免第三方沙箱）调度标准通道：
1. V2EX 极客与开发者热议 (`agent-reach v2ex hot`)
2. Bilibili 科技与开源视频热点 (`agent-reach bilibili search/hot`)
3. 结构化 JSON 标准输出解析与超时降级
"""

import os
import sys
import json
import shutil
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("AgentReachAdapter")

AGENT_REACH_BIN = shutil.which("agent-reach") or str(Path.home() / ".local" / "bin" / "agent-reach")

def is_agent_reach_available() -> bool:
    """检查系统 agent-reach CLI 是否就绪"""
    return bool(AGENT_REACH_BIN and os.path.exists(AGENT_REACH_BIN))

def fetch_v2ex_hot(limit: int = 5) -> List[Dict[str, Any]]:
    """
    通过 Agent Reach 1.6+ 统一命令获取 V2EX 热门开发者话题
    """
    if not is_agent_reach_available():
        return []

    cmd = [AGENT_REACH_BIN, "v2ex", "hot"]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            items = data.get("items", [])
            results = []
            for t in items[:limit]:
                raw_node = t.get("raw", {}).get("node", {})
                node_title = raw_node.get("title") or raw_node.get("name") or "Tech"
                replies = t.get("raw", {}).get("replies", 0)
                results.append({
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "node": node_title,
                    "replies": replies,
                    "platform": "v2ex"
                })
            return results
    except Exception as e:
        logger.warning(f"V2EX fetch via Agent Reach failed: {e}")
    return []

def fetch_bilibili_tech_hot(keywords: str = "AI开源", limit: int = 4) -> List[Dict[str, Any]]:
    """
    通过 Agent Reach 1.6+ 统一命令获取 B站 科技热门视频
    """
    if not is_agent_reach_available():
        return []

    cmd = [AGENT_REACH_BIN, "bilibili", "search", keywords]
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8
        )
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout.strip())
            items = data.get("items", [])
            results = []
            for v in items[:limit]:
                results.append({
                    "title": v.get("title", ""),
                    "url": v.get("url", ""),
                    "author": v.get("author", ""),
                    "platform": "bilibili"
                })
            return results
    except Exception as e:
        logger.warning(f"Bilibili fetch via Agent Reach failed: {e}")
    return []

def fetch_agent_reach_trends() -> Dict[str, List[Dict[str, Any]]]:
    """
    统一入口：获取 Agent Reach 1.6+ 极客与开发者热点
    """
    return {
        "v2ex": fetch_v2ex_hot(limit=4),
        "bilibili": fetch_bilibili_tech_hot(keywords="AI开源", limit=3)
    }

