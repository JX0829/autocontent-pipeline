#!/usr/bin/env python3
"""速报改造回归（SPEC-20261114 · T1~T5 + A1/A4 的逻辑级断言）。

不联网：所有信源用夹具替代，才能断言「同一份输入 → 新旧实现 unique_id 完全相同」。
真实抓取的回归在 T7（`--live` 档，见文末）。

用法：
    python3 scripts/test_digest_revamp.py          # 全量（<2s，不联网）
    python3 scripts/test_digest_revamp.py -v       # 打印每条断言
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from zoneinfo import ZoneInfo                                        # noqa: E402

from src.digest import config as dcfg                                 # noqa: E402
from src.digest import orchestrator as orch                           # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=TZ)
ORIG_FETCHERS = dict(orch.FETCHERS)
ORIG_FETCH_X = orch.fetch_x_pool
RESULTS: List[tuple[str, bool, str]] = []


def restore_new() -> None:
    """每个用例开始前复位，防止上一个用例的 monkeypatch 串味（本 session 已踩过）。"""
    orch.FETCHERS.clear()
    orch.FETCHERS.update(ORIG_FETCHERS)
    orch.fetch_x_pool = ORIG_FETCH_X  # type: ignore[assignment]
VERBOSE = "-v" in sys.argv


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    if VERBOSE or not cond:
        print(f"{'PASS' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


def _iso(hours_ago: float) -> str:
    # 真实数据里 Reddit 是空格格式、X 是 RFC2822；用空格格式才能走到 parse_to_cst 成功分支
    return (NOW - timedelta(hours=hours_ago)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")


def item(uid: str, title: str, source: str, hours_ago: float = 2, **raw: Any) -> Dict[str, Any]:
    return {
        "title": title, "desc": f"{title} 描述", "url": f"https://example.com/{uid}",
        "source": source, "author": "tester", "published_at": "今日实时", "badge": source,
        "unique_id": uid, "raw": {"created_at": _iso(hours_ago), "published_at": _iso(hours_ago), **raw},
    }


# ── 夹具：同一份输入喂给新旧两套实现 ───────────────────────────
def fixtures() -> Dict[str, List[Dict[str, Any]]]:
    return {
        "blog": [item("bl_1", "OpenAI 官方发布新模型", "blog", 3),
                 item("bl_2", "Anthropic 官方公告", "blog", 5)],
        "x": [item("tw_1", "NVIDIA 发布新卡", "x", 1),
              item("tw_2", "OpenAI 宣布功能", "x", 2),
              item("tw_3", "某旧推文", "x", 90)],
        "hf": [item("hf_1", "HF 论文 A", "hf", 10)],
        "arxiv": [item("ax_1", "arXiv 论文 B", "arxiv", 20)],
        "github": [item("gh_low", "开源爆款: a/low", "github", 1, stars_today=12, repo="a/low"),
                   item("gh_high", "开源爆款: b/high", "github", 1, stars_today=900, repo="b/high"),
                   item("gh_mid", "开源爆款: c/mid", "github", 1, stars_today=300, repo="c/mid")],
        "reddit": [item("rd_1", "社区热议: b/high 太强了", "reddit", 4, raw_title="b/high 太强了"),
                   item("rd_2", "社区热议: 本地跑 70B", "reddit", 6, raw_title="本地跑 70B"),
                   item("rd_3", "社区热议: 十天前的老帖", "reddit", 240, raw_title="十天前的老帖")],
    }


def load_legacy():
    """冻结的迁移前实现（tests/fixtures/legacy_fetch_trends.py，来自 11-11 17:42 备份）。"""
    path = PROJ / "tests" / "fixtures" / "legacy_fetch_trends.py"
    spec = importlib.util.spec_from_file_location("legacy_fetch_trends", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def patch_legacy(legacy, fx, tmp: Path) -> None:
    legacy.fetch_official_blog_posts = lambda **kw: copy.deepcopy(fx["blog"])
    legacy.fetch_twitter_x_tech = lambda **kw: copy.deepcopy(fx["x"])
    legacy.fetch_huggingface_daily_papers = lambda **kw: copy.deepcopy(fx["hf"])
    legacy.fetch_arxiv_ai_papers = lambda **kw: copy.deepcopy(fx["arxiv"])
    legacy.fetch_github_trending = lambda **kw: copy.deepcopy(fx["github"])
    legacy.fetch_reddit_ai_hot = lambda **kw: copy.deepcopy(fx["reddit"])
    legacy.SEEN_FILE = tmp / "legacy_seen.json"
    legacy.SNAPSHOT_FILE = tmp / "legacy_snapshot.json"
    legacy.DATA_DIR = tmp
    legacy.TZ_SHANGHAI = TZ


def patch_new(fx, cfg, calls: Optional[List[str]] = None):
    def spy(name, fn):
        def wrapper(**kw):
            if calls is not None:
                calls.append(name)
            return copy.deepcopy(fx[name])
        return wrapper

    orch.FETCHERS.update({
        "blog": spy("blog", None), "hf": spy("hf", None), "arxiv": spy("arxiv", None),
        "github": spy("github", None), "reddit": spy("reddit", None),
    })
    orch.fetch_x_pool = lambda xcfg, fallback_limit=8: (calls.append("x") if calls is not None else None) or \
        copy.deepcopy(fx["x"])
    return cfg


def base_cfg(tmp: Path) -> Dict[str, Any]:
    cfg = dcfg.load()
    cfg["paths"] = {"seen_file": str(tmp / "seen.json"), "snapshot_file": str(tmp / "snap.json")}
    return cfg


# ── T1：A/B 等价（默认配置 = 迁移前行为）────────────────────────
def test_t1_parity() -> None:
    restore_new()
    legacy = load_legacy()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        patch_legacy(legacy, fixtures(), tmp)
        legacy.SEEN_FILE.write_text(json.dumps({"gh_low": {"timestamp": 1e9}}), encoding="utf-8")

        old_text, old_snap = legacy.build_unified_hotspot_digest(per_section=3)
        old_uids = [old_snap[k]["unique_id"] for k in sorted(old_snap, key=int)]

        cfg = dcfg.legacy()
        cfg["paths"] = {"seen_file": str(tmp / "new_seen.json"), "snapshot_file": str(tmp / "new_snap.json")}
        Path(cfg["paths"]["seen_file"]).write_text(json.dumps({"gh_low": {"timestamp": 1e9}}), encoding="utf-8")
        patch_new(fixtures(), cfg)
        new_text, new_snap = orch.build_unified_hotspot_digest(cfg)
        new_uids = [new_snap[k]["unique_id"] for k in sorted(new_snap, key=int)]

    check("T1 A/B unique_id 序列完全一致", old_uids == new_uids, f"old={old_uids} new={new_uids}")
    check("T1 A/B 正文完全一致", old_text == new_text,
          "" if old_text == new_text else "正文有差异（格式漂移）")
    check("T1 默认配置仍是 4 板块 9 条节奏", len(old_snap) == 9, f"实际 {len(old_snap)} 条")


# ── T2：papers 开关 ────────────────────────────────────────────
def test_t2_papers_off() -> None:
    restore_new()
    calls: List[str] = []
    with tempfile.TemporaryDirectory() as d:
        cfg = base_cfg(Path(d))
        patch_new(fixtures(), cfg, calls)
        text, snap = orch.build_unified_hotspot_digest(cfg)
    check("T2 papers=false 时不调用 HF/arXiv", "hf" not in calls and "arxiv" not in calls, f"calls={calls}")
    check("T2 输出无论文条目", not any("论文" in snap[k]["title"] for k in snap),
          str([snap[k]["title"] for k in snap]))
    check("T2 正文只有 2 个板块", text.count("\n## ") == 2, f"实际 {text.count(chr(10) + '## ')}")


# ── T3：去重窗口 / 顺延补齐 / Reddit 时效 / GitHub 排序 ─────────
def test_t3_dedupe() -> None:
    restore_new()
    fresh = [item("a", "甲", "github", 1), item("b", "乙", "github", 1)]
    seen = {"a": {"timestamp": 0}, "b": {"timestamp": 0}}
    with_gap = orch.filter_and_dedup(copy.deepcopy(fresh), seen, count=2, fill_gap=True)
    no_gap = orch.filter_and_dedup(copy.deepcopy(fresh), seen, count=2, fill_gap=False)
    check("T3 宁缺勿凑：新鲜不足 2 条时只出 0 条", len(no_gap) == 0, f"fill_gap=False → {len(no_gap)} 条")
    check("T3 旧行为仍会顺延补齐（对照）", len(with_gap) == 2, f"fill_gap=True → {len(with_gap)} 条")

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "seen.json"
        now = 1_800_000_000.0
        orig = orch.time.time
        orch.time.time = lambda: now            # type: ignore[assignment]
        try:
            orch.save_seen_trends({"old3": {"timestamp": now - 3 * 86400},
                                   "old6": {"timestamp": now - 6 * 86400}}, p, window_days=5)
        finally:
            orch.time.time = orig               # type: ignore[assignment]
        kept = set(json.loads(p.read_text(encoding="utf-8")))
    check("T3 5 天窗口保留 3 天前的记录", "old3" in kept, str(kept))
    check("T3 5 天窗口清掉 6 天前的记录", "old6" not in kept, str(kept))

    fx = fixtures()
    reddit_fresh = orch.filter_and_dedup(copy.deepcopy(fx["reddit"]), {}, count=3, max_days=2, now_cst=NOW)
    check("T3 Reddit 只保留近 2 天", [i["unique_id"] for i in reddit_fresh] == ["rd_1", "rd_2"],
          str([i["unique_id"] for i in reddit_fresh]))

    with tempfile.TemporaryDirectory() as d:
        cfg = base_cfg(Path(d))
        patch_new(fx, cfg)
        _, snap = orch.build_unified_hotspot_digest(cfg)
    gh = [snap[k]["unique_id"] for k in sorted(snap, key=int) if snap[k]["source"] == "github"]
    check("T3 GitHub 按今日涨星排序（900 > 300）", gh[:2] == ["gh_high", "gh_mid"], str(gh))


# ── T4：X 三组查询 ─────────────────────────────────────────────
def test_t4_x_queries() -> None:
    restore_new()
    calls: List[str] = []
    fx_batches = {
        "q1": [item("t1", "OpenAI 发布", "x", 3), item("t_dup", "NVIDIA 显卡", "x", 1)],
        "q2": [item("t_dup", "NVIDIA 显卡", "x", 1), item("t2", "Tesla 招人", "x", 2)],
        "q3": [item("t3", "空投 crypto giveaway", "x", 1), item("t4", "launches GA", "x", 4)],
    }

    def fake_x(query=None, limit=5, timeout_sec=15):
        calls.append(query)
        return copy.deepcopy(fx_batches["q1" if "OpenAI" in query else "q2" if "NVIDIA" in query else "q3"])

    orig = orch.fetch_twitter_x_tech
    orch.fetch_twitter_x_tech = fake_x            # type: ignore[assignment]
    try:
        pool = orch.fetch_x_pool({"queries": ["OpenAI OR Anthropic", "NVIDIA OR Tesla", "launches"], 
                                  "exclude": ["crypto", "giveaway"], "limit_per_query": 5, "timeout_sec": 10})
    finally:
        orch.fetch_twitter_x_tech = orig           # type: ignore[assignment]

    uids = [i["unique_id"] for i in pool]
    check("T4 三组查询都被调用", len(calls) == 3, str(calls))
    check("T4 跨查询去重", uids.count("t_dup") == 1, str(uids))
    check("T4 排除词生效（crypto/giveaway）", "t3" not in uids, str(uids))
    check("T4 按发布时间倒序", uids[:2] == ["t_dup", "t2"], str(uids))


# ── T5：2 板块合并池 ──────────────────────────────────────────
def test_t5_merged() -> None:
    restore_new()
    with tempfile.TemporaryDirectory() as d:
        cfg = base_cfg(Path(d))
        patch_new(fixtures(), cfg)
        text, snap = orch.build_unified_hotspot_digest(cfg)
    uids = [snap[k]["unique_id"] for k in sorted(snap, key=int)]
    check("T5 总条数 ≤6", len(uids) <= 6, f"{len(uids)} 条: {uids}")
    check("T5 GitHub 与 Reddit 同时在池子里", {"gh_high", "rd_2"} <= set(uids), str(uids))
    check("T5 跨源同实体只留一条（rd_1 讲 b/high）", "rd_1" not in uids, str(uids))
    check("T5 板块标题带合并池说明", "合并池" in text, "")
    check("T5 快照编号从 1 连续", sorted(snap, key=int) == [str(i) for i in range(1, len(snap) + 1)], str(sorted(snap, key=int)))


# ── A4：快照兼容 ─────────────────────────────────────────────
def test_a4_snapshot() -> None:
    restore_new()
    with tempfile.TemporaryDirectory() as d:
        cfg = base_cfg(Path(d))
        patch_new(fixtures(), cfg)
        _, snap = orch.build_unified_hotspot_digest(cfg)
        on_disk = json.loads(Path(cfg["paths"]["snapshot_file"]).read_text(encoding="utf-8"))
    first = on_disk.get("1") or {}
    check("A4 顶层是编号 dict", isinstance(on_disk, dict) and on_disk and all(k.isdigit() for k in on_disk), str(list(on_disk)[:3]))
    check("A4 取 \"1\" 的 url/title/source 齐全", all(first.get(f) for f in ("url", "title", "source")),
          str({f: bool(first.get(f)) for f in ("url", "title", "source")}))
    check("A4 每条含 unique_id/published_at/formatted_cst/badge",
          all(k in first for k in ("unique_id", "published_at", "formatted_cst", "badge")), str(sorted(first)))


# ── A1/A4 真实抓取（T7 用；--live 才跑）───────────────────────
def live_round(label: str, writing: bool = False):
    cfg = dcfg.load()
    cfg["paths"] = {"seen_file": "/tmp/digest_live_seen.json",
                    "snapshot_file": "/tmp/digest_live_snap.json"}
    text, snap = orch.build_unified_hotspot_digest(cfg, writing=writing)
    return text, snap


def main() -> int:
    for fn in (test_t1_parity, test_t2_papers_off, test_t3_dedupe, test_t4_x_queries,
               test_t5_merged, test_a4_snapshot):
        try:
            fn()
        except Exception:
            check(f"{fn.__name__} 未抛异常", False, traceback.format_exc(limit=2).replace("\n", " | "))
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, d) for l, c, d in RESULTS if not c]
    print(f"\nDIGEST CONTRACT {ok} PASS / {len(bad)} FAIL")
    if bad:
        for l, d in bad:
            print(f"  ✗ {l} {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
