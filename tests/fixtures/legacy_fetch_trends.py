#!/usr/bin/env python3
"""冻结的「迁移前实现」副本（SPEC-20261114 · T1 的 A/B 基线）。

来源：2026-11-14 覆盖前的
`agents_context/xiao-pin/scripts/fetch_trends.py`（4 板块 9 条版本，逐行照抄）。
只供 `scripts/test_digest_revamp.py` 做新旧对照，**不要**被业务代码 import。
"""
import datetime
import json
import os
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

PIPELINE_ROOT = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PIPELINE_ROOT))

from src.crawlers.atomic_fetchers import (  # noqa: E402
    fetch_arxiv_ai_papers,
    fetch_github_trending,
    fetch_huggingface_daily_papers,
    fetch_official_blog_posts,
    fetch_reddit_ai_hot,
    fetch_twitter_x_tech,
    translate_to_zh,
)

DATA_DIR = Path("/Users/a1/Downloads/workspace/team/agents_context/xiao-pin/data")
SEEN_FILE = DATA_DIR / "seen_trends.json"
SNAPSHOT_FILE = DATA_DIR / "last_digest_snapshot.json"

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")


def parse_to_cst(dt_str: Any) -> Optional[datetime.datetime]:
    if not dt_str:
        return None
    dt_str = str(dt_str).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(dt_str[:19], fmt)
            return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ_SHANGHAI)
        except Exception:
            pass
    try:
        dt = parsedate_to_datetime(dt_str)
        return dt.astimezone(TZ_SHANGHAI)
    except Exception:
        pass
    return None


def is_within_days(dt: Optional[datetime.datetime], days: int, now_cst: datetime.datetime) -> bool:
    if not dt:
        return True
    delta = now_cst - dt
    return -datetime.timedelta(hours=2) <= delta <= datetime.timedelta(days=days)


def load_seen_trends() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_seen_trends(seen_dict: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now_ts = time.time()
    cleaned = {k: v for k, v in seen_dict.items() if (now_ts - v.get("timestamp", 0)) < 172800}
    SEEN_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_item_as_seen(seen_dict: dict, item_id: str, title: str):
    seen_dict[item_id] = {
        "title": title,
        "timestamp": time.time(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_digest_snapshot(snapshot: Dict[str, Any]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def get_digest_item_by_index(idx_str: str) -> Optional[Dict[str, Any]]:
    if not SNAPSHOT_FILE.exists():
        return None
    try:
        snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        clean_key = str(idx_str).strip()
        return snapshot.get(clean_key)
    except Exception:
        return None


def filter_and_dedup(
    candidates: List[Dict[str, Any]],
    seen_dict: dict,
    count: int,
    max_days: Optional[int] = None,
    now_cst: Optional[datetime.datetime] = None
) -> List[Dict[str, Any]]:
    if now_cst is None:
        now_cst = datetime.datetime.now(TZ_SHANGHAI)

    valid_time_candidates = []
    for it in candidates:
        raw_info = it.get("raw", {})
        raw_pub = raw_info.get("created_at") or raw_info.get("published_at") or it.get("published_at")
        dt_cst = parse_to_cst(raw_pub)
        if max_days is not None:
            if not is_within_days(dt_cst, max_days, now_cst):
                continue

        if dt_cst:
            it["formatted_cst"] = dt_cst.strftime("%Y-%m-%d %H:%M (UTC+8)")
        else:
            it["formatted_cst"] = "今日实时 (UTC+8)"
        valid_time_candidates.append(it)

    fresh = []
    for it in valid_time_candidates:
        uid = it.get("unique_id", it.get("url"))
        if uid not in seen_dict:
            fresh.append(it)
            if len(fresh) >= count:
                break

    if len(fresh) < count:
        for it in valid_time_candidates:
            if it not in fresh:
                fresh.append(it)
            if len(fresh) >= count:
                break

    return fresh[:count]


def build_unified_hotspot_digest(per_section: int = 3) -> Tuple[str, Dict[str, Any]]:
    now_cst = datetime.datetime.now(TZ_SHANGHAI)
    date_str = now_cst.strftime("%Y-%m-%d %H:%M (UTC+8)")
    seen_dict = load_seen_trends()

    tw_raw = fetch_twitter_x_tech(limit=8)
    blog_raw = fetch_official_blog_posts(limit=4, timeout_sec=25, max_days=7)
    hf_raw = fetch_huggingface_daily_papers(limit=6, timeout_sec=30)
    arxiv_raw = fetch_arxiv_ai_papers(limit=6, timeout_sec=30)
    gh_raw = fetch_github_trending(limit=6, timeout_sec=20)
    reddit_raw = fetch_reddit_ai_hot(limit=6, timeout_sec=20)

    blog_items = filter_and_dedup(blog_raw, seen_dict, count=1, max_days=7, now_cst=now_cst)
    tw_items = filter_and_dedup(tw_raw, seen_dict, count=3 - len(blog_items), max_days=2, now_cst=now_cst)
    section1_items = blog_items + tw_items

    hf_items = filter_and_dedup(hf_raw, seen_dict, count=1, max_days=7, now_cst=now_cst)
    arxiv_items = filter_and_dedup(arxiv_raw, seen_dict, count=1, max_days=7, now_cst=now_cst)
    paper_items = hf_items + arxiv_items

    gh_items = filter_and_dedup(gh_raw, seen_dict, count=2, max_days=None, now_cst=now_cst)
    reddit_items = filter_and_dedup(reddit_raw, seen_dict, count=2, max_days=None, now_cst=now_cst)

    lines = [f"# 🚀 【全网前沿 AI 实时技术与热点综合速报】({date_str})\n"]
    snapshot: Dict[str, Any] = {}
    current_index = 1

    sections = [
        ("🐦 Section 1: 官方公告 + X 全球先锋动态 (官方优先 · 共 3 条)", section1_items, "查看原文"),
        ("📚 Section 2: 前沿算法与顶会论文 (HF 1 + arXiv 1 · 共 2 条 · 近 7 天)", paper_items, "查看论文/成果"),
        ("💻 Section 3: 开源爆款 (GitHub · 共 2 条)", gh_items, "查看源码"),
        ("🔥 Section 4: 极客社区热议 (Reddit · 共 2 条)", reddit_items, "查看讨论"),
    ]

    for sec_title, items, link_label in sections:
        if not items:
            lines.append(f"## {sec_title}")
            lines.append("- ⚠️ **本轮该板块上游抓取为空（超时或无符合时效的新增条目），已跳过，非内容缺失。**\n")
            continue
        lines.append(f"## {sec_title}")
        for it in items:
            idx_str = str(current_index)
            snapshot[idx_str] = it

            zh_title = translate_to_zh(it.get("title", ""))
            zh_desc = translate_to_zh(it.get("desc", ""))
            badge_str = f" ({it.get('badge')})" if it.get("badge") else ""

            lines.append(f"### {idx_str}. {zh_title}{badge_str}")
            lines.append(f"- **核心亮点**: {zh_desc}")
            lines.append(f"- **发布时间**: `{it.get('formatted_cst', '今日实时 (UTC+8)')}`")
            lines.append(f"- **原文链接**: [{link_label}]({it['url']})\n")

            uid = str(it.get("unique_id") or it.get("url") or "")
            mark_item_as_seen(seen_dict, uid, zh_title)
            current_index += 1

    lines.append("───────────────────────────────")
    lines.append("🎬 **一键制片**：直接在群聊中回复对应编号（如 `3` 或 `做3`），即可立刻自动锁定该 AI 热点生成短视频！")

    save_seen_trends(seen_dict)
    save_digest_snapshot(snapshot)

    full_text = "\n".join(lines)
    return full_text, snapshot


def main():
    text, snapshot = build_unified_hotspot_digest(per_section=3)
    print(text)
    print(f"\n[Info: Snapshot successfully saved with {len(snapshot)} items]")


if __name__ == "__main__":
    main()
