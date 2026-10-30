"""
src/crawlers/agent_reach_adapter.py
Agent Reach 核心能力适配器
通过挂接 ~/.agent-reach-venv 环境，免代理、免爬虫无痕获取：
1. V2EX 开发者社区热门主题与极客讨论 (V2EXChannel)
2. 雪球科技/商业热议与硬核产业讨论 (XueqiuChannel)
3. 扩展社交与网页阅读路由
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger("AgentReachAdapter")

AGENT_REACH_PYTHON = Path.home() / ".agent-reach-venv" / "bin" / "python3.14"

def is_agent_reach_available() -> bool:
    """检查本地 Agent Reach 运行环境是否完备"""
    return AGENT_REACH_PYTHON.exists()

def fetch_v2ex_hot(limit: int = 5) -> List[Dict[str, Any]]:
    """
    通过 Agent Reach 获取 V2EX 热门开发者话题
    """
    if not is_agent_reach_available():
        return []

    code = f"""
import json
from agent_reach.channels.v2ex import V2EXChannel
v = V2EXChannel()
hot = v.get_hot_topics(limit={limit})
items = []
for t in hot:
    items.append({{
        "title": t.get("title", ""),
        "url": t.get("url", ""),
        "node": t.get("node_title", ""),
        "replies": t.get("replies", 0),
        "platform": "v2ex"
    }})
print(json.dumps(items, ensure_ascii=False))
"""
    try:
        res = subprocess.run(
            [str(AGENT_REACH_PYTHON), "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8
        )
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip())
    except Exception as e:
        logger.warning(f"V2EX fetch via Agent Reach failed: {e}")
    return []

def fetch_xueqiu_tech_hot(limit: int = 5) -> List[Dict[str, Any]]:
    """
    通过 Agent Reach 获取雪球科技/商业产业热议
    """
    if not is_agent_reach_available():
        return []

    code = f"""
import json
from agent_reach.channels.xueqiu import XueqiuChannel
xq = XueqiuChannel()
posts = xq.get_hot_posts()
tech_keywords = ["ai", "大模型", "芯片", "算力", "英伟达", "半导体", "机器人", "开源", "智能", "算法", "科技"]
items = []
for p in posts:
    t = p.get("title", "") or ""
    txt = p.get("text", "") or ""
    combined = (t + " " + txt).lower()
    if any(k in combined for k in tech_keywords):
        items.append({{
            "title": t if t else txt[:60],
            "url": p.get("url", ""),
            "author": p.get("user", {{}}).get("screen_name", ""),
            "platform": "xueqiu"
        }})
    if len(items) >= {limit}:
        break
print(json.dumps(items, ensure_ascii=False))
"""
    try:
        res = subprocess.run(
            [str(AGENT_REACH_PYTHON), "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8
        )
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Xueqiu fetch via Agent Reach failed: {e}")
    return []

def fetch_agent_reach_trends() -> Dict[str, List[Dict[str, Any]]]:
    """
    统一入口：获取 Agent Reach 渠道极客与商业热点
    """
    return {
        "v2ex": fetch_v2ex_hot(limit=4),
        "xueqiu": fetch_xueqiu_tech_hot(limit=3)
    }
