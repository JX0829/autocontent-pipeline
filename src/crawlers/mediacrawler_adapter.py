"""
src/crawlers/mediacrawler_adapter.py
官方 MediaCrawler 适配器
通过隔离子进程调用 MediaCrawler 命令行，实现指定平台的关键词只读搜索，
并支持将推文/笔记结果标准化结构化返回，供热点雷达与选题流水线消费。
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("MediaCrawlerAdapter")

PIPELINE_ROOT = Path(__file__).resolve().parent.parent.parent
MEDIACRAWLER_DIR = PIPELINE_ROOT / "external" / "MediaCrawler"
VENV_PYTHON = MEDIACRAWLER_DIR / ".venv" / "bin" / "python"

def is_mediacrawler_available() -> bool:
    """检查 MediaCrawler 独立虚拟环境是否就绪"""
    return VENV_PYTHON.exists() and (MEDIACRAWLER_DIR / "main.py").exists()

def search_media_trending(
    platform: str = "xhs",
    keywords: str = "AI,大模型,开源",
    max_count: int = 5,
    timeout_sec: int = 90
) -> List[Dict[str, Any]]:
    """
    通用搜索适配器：
    调用 MediaCrawler 官方 search 命令行获取内容
    :param platform: xhs | dy | bili | wb | tieba | zhihu
    :param keywords: 搜索关键词（英文逗号隔开）
    :param max_count: 最大爬取条数
    :param timeout_sec: 超时时间（秒）
    :return: 规范化的结果列表 [{'title': ..., 'desc': ..., 'url': ..., 'platform': ...}]
    """
    if not is_mediacrawler_available():
        logger.warning(f"MediaCrawler venv or main.py not found at {MEDIACRAWLER_DIR}")
        return []

    # 规范化输出目录
    output_dir = PIPELINE_ROOT / "data" / "crawler_output" / platform
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        str(VENV_PYTHON),
        "main.py",
        f"--platform={platform}",
        "--type=search",
        f"--keywords={keywords}",
        f"--crawler_max_notes_count={max_count}",
        "--save_data_option=jsonl",
        f"--save_data_path={output_dir}",
        "--headless=true",
        "--get_comment=false",
        "--get_sub_comment=false",
        "--get_media=false"
    ]

    logger.info(f"Executing MediaCrawler: platform={platform}, keywords={keywords}, max_count={max_count}")
    try:
        res = subprocess.run(
            cmd,
            cwd=str(MEDIACRAWLER_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec
        )
        if res.returncode != 0:
            logger.warning(f"MediaCrawler exited with code {res.returncode}: {res.stderr[:300]}")
    except subprocess.TimeoutExpired:
        logger.warning(f"MediaCrawler execution timed out after {timeout_sec}s")
    except Exception as e:
        logger.error(f"Error running MediaCrawler subprocess: {e}")

    # 读取生成的 jsonl 数据
    results: List[Dict[str, Any]] = []
    try:
        # 查找最新生成的 jsonl 文件
        jsonl_files = sorted(output_dir.glob("*.jsonl"), key=os.path.getmtime, reverse=True)
        if jsonl_files:
            latest_file = jsonl_files[0]
            with open(latest_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        results.append({
                            "title": item.get("title") or item.get("desc") or "无标题",
                            "desc": item.get("desc") or "",
                            "url": item.get("note_url") or item.get("url") or "",
                            "platform": platform,
                            "liked_count": item.get("liked_count", 0),
                            "author": item.get("nickname") or item.get("user_id") or ""
                        })
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"Failed to parse MediaCrawler jsonl output: {e}")

    return results
