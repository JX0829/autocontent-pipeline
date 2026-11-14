"""速报编排 / 去重 / 板块 / 快照（SPEC-20261114 · T1~T5）。

从 `agents_context/xiao-pin/scripts/fetch_trends.py` 迁入，行为由 `src.digest.config` 决定：
- 配置缺失 → `config.legacy()`（迁移前行为：4 板块 9 条、48h 窗口、顺延补齐）
- `config/trends.yaml` → 新口径（2 板块 5~6 条、5 天窗口、宁缺勿凑、X 三组查询）

快照结构不变（顶层 `{"1": item, ...}`），「回复编号制片」链路零改动。
"""
from __future__ import annotations

import datetime
import json
import logging
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from src.crawlers.atomic_fetchers import (
    fetch_arxiv_ai_papers,
    fetch_github_trending,
    fetch_huggingface_daily_papers,
    fetch_official_blog_posts,
    fetch_reddit_ai_hot,
    fetch_twitter_x_tech,
    translate_to_zh,
)

from . import config as digest_config

TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)
PAPER_SOURCES = ("hf", "arxiv")

# 信源 → 抓取函数。签名不同，统一在这里适配，编排层只见 (limit, timeout_sec)。
FETCHERS: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "blog": lambda limit, timeout_sec, **_: fetch_official_blog_posts(limit=limit, timeout_sec=timeout_sec),
    "x": lambda limit, timeout_sec, query=None, **_: (
        fetch_twitter_x_tech(query=query, limit=limit, timeout_sec=timeout_sec)
        if query
        else fetch_twitter_x_tech(limit=limit, timeout_sec=timeout_sec)
    ),
    "hf": lambda limit, timeout_sec, **_: fetch_huggingface_daily_papers(limit=limit, timeout_sec=timeout_sec),
    "arxiv": lambda limit, timeout_sec, **_: fetch_arxiv_ai_papers(limit=limit, timeout_sec=timeout_sec),
    "github": lambda limit, timeout_sec, **_: fetch_github_trending(limit=limit, timeout_sec=timeout_sec),
    "reddit": lambda limit, timeout_sec, **_: fetch_reddit_ai_hot(limit=limit, timeout_sec=timeout_sec),
}


def _warn(msg: str) -> None:
    """告警必须落到 stderr —— cron 是 no_agent 纯脚本，stdout 就是速报正文。"""
    print(f"[WARN] {msg}", file=sys.stderr)


# ── 时间 ────────────────────────────────────────────────────────
def parse_to_cst(dt_str: Any) -> Optional[datetime.datetime]:
    if not dt_str:
        return None
    dt_str = str(dt_str).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.datetime.strptime(dt_str[:19], fmt)
            return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ_SHANGHAI)
        except Exception as exc:
            logger.debug(f"时间格式 {fmt} 解析失败（{dt_str[:19]}）：{type(exc).__name__}")
            continue
    try:
        return parsedate_to_datetime(dt_str).astimezone(TZ_SHANGHAI)
    except Exception as exc:
        logger.debug(f"时间戳无法解析（{dt_str[:32]}）：{type(exc).__name__}: {exc}")
        return None


def is_within_days(dt: Optional[datetime.datetime], days: int, now_cst: datetime.datetime) -> bool:
    if not dt:
        return True  # 没有时间戳的条目交给去重库管，不在这里误杀
    delta = now_cst - dt
    return -datetime.timedelta(hours=2) <= delta <= datetime.timedelta(days=days)


# ── 状态：去重库 + 快照 ──────────────────────────────────────────
def load_seen_trends(path: Optional[str | Path] = None) -> Dict[str, Any]:
    p = Path(path) if path else Path(digest_config.DEFAULTS["paths"]["seen_file"])
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            _warn(f"去重库解析失败（按空库处理）：{p} · {type(exc).__name__}: {exc}")
            return {}
    _warn(f"去重库不存在，本轮按空库处理：{p}")
    return {}


def save_seen_trends(seen_dict: Dict[str, Any], path: Optional[str | Path] = None,
                     window_days: Optional[float] = None) -> Path:
    days = (float(window_days) if window_days is not None
            else float(digest_config.legacy()["dedupe_window_days"]))
    p = Path(path) if path else Path(digest_config.DEFAULTS["paths"]["seen_file"])
    p.parent.mkdir(parents=True, exist_ok=True)
    now_ts = time.time()
    window_sec = float(days) * 86400
    cleaned = {k: v for k, v in seen_dict.items() if (now_ts - v.get("timestamp", 0)) < window_sec}
    p.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def mark_item_as_seen(seen_dict: Dict[str, Any], item_id: str, title: str) -> None:
    seen_dict[item_id] = {
        "title": title,
        "timestamp": time.time(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_digest_snapshot(snapshot: Dict[str, Any], path: Optional[str | Path] = None) -> Path:
    p = Path(path) if path else Path(digest_config.DEFAULTS["paths"]["snapshot_file"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def get_digest_item_by_index(idx_str: str, path: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
    """按用户回复的编号（'3' / '做3' → 3）从最近一期快照反查条目。"""
    p = Path(path) if path else Path(digest_config.DEFAULTS["paths"]["snapshot_file"])
    if not p.exists():
        return None
    try:
        snapshot = json.loads(p.read_text(encoding="utf-8"))
        return snapshot.get(str(idx_str).strip())
    except Exception as exc:
        logger.warning(f"快照读取失败（{p}）：{type(exc).__name__}: {exc}")
        return None


# ── 过滤 / 去重 ─────────────────────────────────────────────────
def filter_and_dedup(
    candidates: List[Dict[str, Any]],
    seen_dict: Dict[str, Any],
    count: int,
    max_days: Optional[int] = None,
    now_cst: Optional[datetime.datetime] = None,
    fill_gap: bool = True,
) -> List[Dict[str, Any]]:
    """时效门禁 + 去重。

    fill_gap=False 就是 R3 的「宁缺勿凑」：新鲜条目不够就少出，绝不回捞看过的凑数
    （旧行为反向：从有效候选里顺延补齐，这正是「每天都是那几个」的元凶）。
    """
    if now_cst is None:
        now_cst = datetime.datetime.now(TZ_SHANGHAI)

    valid_time: List[Dict[str, Any]] = []
    for it in candidates:
        raw_info = it.get("raw", {}) or {}
        raw_pub = raw_info.get("created_at") or raw_info.get("published_at") or it.get("published_at")
        dt_cst = parse_to_cst(raw_pub)
        if max_days is not None and not is_within_days(dt_cst, max_days, now_cst):
            continue
        it["formatted_cst"] = (
            dt_cst.strftime("%Y-%m-%d %H:%M (UTC+8)") if dt_cst else "今日实时 (UTC+8)"
        )
        valid_time.append(it)

    fresh: List[Dict[str, Any]] = []
    for it in valid_time:
        uid = it.get("unique_id", it.get("url"))
        if uid not in seen_dict:
            fresh.append(it)
            if len(fresh) >= count:
                break

    if fill_gap and len(fresh) < count:
        for it in valid_time:
            if it not in fresh:
                fresh.append(it)
            if len(fresh) >= count:
                break

    return fresh[:count]


# ── 跨源实体（R1 合并池用）──────────────────────────────────────
def entity_keys(item: Dict[str, Any]) -> List[str]:
    """同一实体的归一化 key：GitHub 用 owner/repo，Reddit 从标题里抽 owner/repo。"""
    raw = item.get("raw", {}) or {}
    keys: List[str] = []
    repo = raw.get("repo") or raw.get("full_name")
    if repo:
        keys.append(str(repo).lower())
    title = f"{raw.get('raw_title', '')} {item.get('title', '')}"
    import re
    for m in re.finditer(r"\b([A-Za-z0-9][\w.-]{0,38}/[A-Za-z0-9][\w.-]{0,38})\b", title):
        keys.append(m.group(1).lower())
    return keys


def merge_pool(items: List[Dict[str, Any]], grouped: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """合并池排序：同源内名次归一化（1.0 = 该源第一）后跨源比较，再按新鲜度断平。

    这样既尊重「GitHub 按今日涨星、Reddit 按新鲜度」各自的排序口径，又不需要把
    stars 和 reddit score 硬凑成一个量纲。
    """
    rank: Dict[int, float] = {}
    for src, lst in grouped.items():
        n = max(len(lst), 1)
        for i, it in enumerate(lst):
            rank[id(it)] = 1.0 - (i / n)

    def recency(it: Dict[str, Any]) -> float:
        dt = parse_to_cst((it.get("raw") or {}).get("published_at") or it.get("published_at"))
        return dt.timestamp() if dt else 0.0

    return sorted(items, key=lambda it: (-rank.get(id(it), 0.0), -recency(it)))


def _stars_today(item: Dict[str, Any]) -> int:
    """真实数据里 stars_today 是带千分位的字符串（'1,925'），不能直接 int()。"""
    import re
    raw = (item.get("raw") or {}).get("stars_today")
    if isinstance(raw, (int, float)):
        return int(raw)
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    return int(digits) if digits else 0


def _sort_source(name: str, items: List[Dict[str, Any]], cfg_src: Dict[str, Any]) -> List[Dict[str, Any]]:
    if cfg_src.get("sort") == "stars_today":
        return sorted(items, key=_stars_today, reverse=True)
    return items


# ── X 三组查询（R4）─────────────────────────────────────────────
def fetch_x_pool(xcfg: Dict[str, Any], fallback_limit: int = 8) -> List[Dict[str, Any]]:
    queries = [q for q in (xcfg.get("queries") or []) if str(q).strip()]
    if not queries:  # 旧行为：一次默认查询
        return fetch_twitter_x_tech(limit=fallback_limit)

    exclude = [e.lower() for e in (xcfg.get("exclude") or [])]
    limit = int(xcfg.get("limit_per_query", 5))
    timeout = int(xcfg.get("timeout_sec", 10))

    merged: List[Dict[str, Any]] = []
    seen_uid = set()
    for q in queries:
        batch = fetch_twitter_x_tech(query=q, limit=limit, timeout_sec=timeout)
        print(f"[X] query={q!r} → {len(batch)} 条", file=sys.stderr)
        for it in batch:
            title_desc = f"{it.get('title', '')} {it.get('desc', '')}".lower()
            if any(word and word in title_desc for word in exclude):
                continue
            uid = it.get("unique_id") or it.get("url")
            if uid in seen_uid:
                continue
            seen_uid.add(uid)
            merged.append(it)

    merged.sort(key=lambda it: (parse_to_cst(
        (it.get("raw") or {}).get("created_at") or it.get("published_at")) or datetime.datetime(1970, 1, 1, tzinfo=TZ_SHANGHAI)
    ), reverse=True)
    return merged


# ── 板块 ────────────────────────────────────────────────────────
def _collect_section(sec: Dict[str, Any], cfg: Dict[str, Any], seen: Dict[str, Any],
                     now_cst: datetime.datetime) -> Tuple[List[Dict[str, Any]], List[str]]:
    """按板块配置抓取 + 过滤，返回 (条目, 告警)。"""
    notes: List[str] = []
    total = int(sec["total"])
    used = 0
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    pool: List[Dict[str, Any]] = []

    for src in sec["sources"]:
        name = src["name"]
        if name in PAPER_SOURCES and not cfg.get("papers", True):
            continue
        count = src.get("count", None)
        want = (total - used) if count is None else int(count)
        if want <= 0:
            continue
        try:
            if name == "x":
                raw = fetch_x_pool(cfg.get("x") or {}, fallback_limit=int(src.get("limit", 8)))
            else:
                raw = FETCHERS[name](limit=int(src.get("limit", 6)),
                                     timeout_sec=src.get("timeout_sec", 20),
                                     max_days=src.get("max_days"))
        except Exception as exc:  # 单源失败不拖垮整轮
            notes.append(f"信源 {name} 抓取异常：{type(exc).__name__}: {exc}")
            _warn(notes[-1])
            raw = []

        raw = _sort_source(name, raw, src)
        picked = filter_and_dedup(raw, seen, count=want,
                                  max_days=src.get("max_days"),
                                  now_cst=now_cst,
                                  fill_gap=bool(cfg.get("fill_gap", True)))
        grouped[name] = picked
        pool.extend(picked)
        used += len(picked)

    if sec.get("merge"):
        pool = merge_pool(pool, grouped)
        # 跨源同一实体只留一条（GitHub 星标数据更硬，保留先出现的 GitHub 条目）
        kept: List[Dict[str, Any]] = []
        seen_entities: set = set()
        for it in pool:
            keys = set(entity_keys(it))
            if keys & seen_entities:
                notes.append(f"跨源去重：{it.get('unique_id')} 与已选条目同实体，跳过")
                continue
            seen_entities |= keys
            kept.append(it)
        pool = kept[:total]
        if len(pool) < int(sec.get("min_expected", 1)):
            notes.append(f"板块「{sec['id']}」只凑到 {len(pool)} 条（期望 ≥{sec.get('min_expected')}）："
                         f"宁缺勿凑，未用已看过的条目补齐")
    else:
        pool = pool[:total]

    return pool, notes


def render_digest(sections_out: List[Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]],
                  now_cst: datetime.datetime) -> Tuple[str, Dict[str, Any]]:
    date_str = now_cst.strftime("%Y-%m-%d %H:%M (UTC+8)")
    lines = [f"# 🚀 【全网前沿 AI 实时技术与热点综合速报】({date_str})\n"]
    snapshot: Dict[str, Any] = {}
    idx = 1
    for sec, items, notes in sections_out:
        lines.append(f"## {sec['title']}")
        if not items:
            lines.append("- ⚠️ **本轮该板块上游抓取为空（超时或无符合时效的新增条目），已跳过，非内容缺失。**\n")
            continue
        if notes:
            lines.append(f"- ⚠️ {'；'.join(notes)}")
        for it in items:
            key = str(idx)
            snapshot[key] = it
            zh_title = translate_to_zh(it.get("title", ""))
            zh_desc = translate_to_zh(it.get("desc", ""))
            badge = f" ({it.get('badge')})" if it.get("badge") else ""
            lines.append(f"### {key}. {zh_title}{badge}")
            lines.append(f"- **核心亮点**: {zh_desc}")
            lines.append(f"- **发布时间**: `{it.get('formatted_cst', '今日实时 (UTC+8)')}`")
            lines.append(f"- **原文链接**: [{sec.get('link_label', '查看原文')}]({it['url']})\n")
            idx += 1
    lines.append("───────────────────────────────")
    lines.append("🎬 **一键制片**：直接在群聊中回复对应编号（如 `3` 或 `做3`），即可立刻自动锁定该 AI 热点生成短视频！")
    return "\n".join(lines), snapshot


def build_unified_hotspot_digest(cfg: Optional[Dict[str, Any]] = None,
                                 writing: bool = True) -> Tuple[str, Dict[str, Any]]:
    """跑一轮速报。cfg=None 时读 config/trends.yaml。

    writing=False 只读（不写去重库/快照），供回归与对比使用。
    """
    cfg = cfg or digest_config.load()
    if not cfg.get("papers", True):
        print(f"papers=off, skipped 2 fetchers ({'/'.join(PAPER_SOURCES)})", file=sys.stderr)
    for msg in digest_config.missing_path_warnings(cfg):
        _warn(msg)

    now_cst = datetime.datetime.now(TZ_SHANGHAI)
    seen_path = cfg["paths"]["seen_file"]
    snap_path = cfg["paths"]["snapshot_file"]
    seen = load_seen_trends(seen_path)

    sections_out = []
    for sec in cfg["sections"]:
        items, notes = _collect_section(sec, cfg, seen, now_cst)
        if not cfg.get("papers", True) and not any(s["name"] not in PAPER_SOURCES for s in sec["sources"]):
            _warn(f"板块「{sec['id']}」只有论文源，papers=off 时整块跳过")
            continue
        for it in items:
            uid = str(it.get("unique_id") or it.get("url") or "")
            if uid:
                mark_item_as_seen(seen, uid, it.get("title", ""))
        sections_out.append((sec, items, notes))

    text, snapshot = render_digest(sections_out, now_cst)
    if writing:
        save_seen_trends(seen, seen_path, float(cfg["dedupe_window_days"]))
        save_digest_snapshot(snapshot, snap_path)
    return text, snapshot


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="科技速报编排（流水线内唯一实现）")
    ap.add_argument("--config", default=None, help="配置文件路径，默认 config/trends.yaml")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写去重库/快照")
    args = ap.parse_args(argv)
    text, snapshot = build_unified_hotspot_digest(digest_config.load(args.config),
                                                  writing=not args.dry_run)
    print(text)
    print(f"\n[Info: Snapshot {'(dry-run 未写)' if args.dry_run else 'successfully saved'} with {len(snapshot)} items]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
