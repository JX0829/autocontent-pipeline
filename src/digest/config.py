"""速报配置加载（SPEC-20261114 · T0）。

设计要点：**代码里的 DEFAULTS 永远等于「迁移前的现行行为」**，`config/trends.yaml`
才是新口径。这样配置文件丢了/写坏了，行为退回老样子（宁可重复多月，也不静默换口径），
而 A/B 对照测试可以直接用 `legacy()` 拿到老行为。

只读配置，不读环境变量 —— 免得新增未声明的 env 键把 GATE-1 打红。
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJ / "config" / "trends.yaml"

# 小品（速报使用方）的资产目录：默认仍指这里，换机器要改 config/trends.yaml
XIAO_PIN_DATA = Path("/Users/a1/Downloads/workspace/team/agents_context/xiao-pin/data")

KNOWN_SOURCES = ("blog", "x", "hf", "arxiv", "github", "reddit")

# 迁移前行为：4 板块 9 条、48h 窗口、顺延补齐、X 只用 fetcher 默认查询
LEGACY_SECTIONS: List[Dict[str, Any]] = [
    {
        "id": "s1",
        "title": "🐦 Section 1: 官方公告 + X 全球先锋动态 (官方优先 · 共 3 条)",
        "link_label": "查看原文",
        "total": 3,
        "sources": [
            {"name": "blog", "count": 1, "max_days": 7, "limit": 4, "timeout_sec": 25},
            {"name": "x", "count": None, "max_days": 2, "limit": 8},
        ],
    },
    {
        "id": "s2",
        "title": "📚 Section 2: 前沿算法与顶会论文 (HF 1 + arXiv 1 · 共 2 条 · 近 7 天)",
        "link_label": "查看论文/成果",
        "total": 2,
        "sources": [
            {"name": "hf", "count": 1, "max_days": 7, "limit": 6, "timeout_sec": 30},
            {"name": "arxiv", "count": 1, "max_days": 7, "limit": 6, "timeout_sec": 30},
        ],
    },
    {
        "id": "s3",
        "title": "💻 Section 3: 开源爆款 (GitHub · 共 2 条)",
        "link_label": "查看源码",
        "total": 2,
        "sources": [{"name": "github", "count": 2, "max_days": None, "limit": 6, "timeout_sec": 20}],
    },
    {
        "id": "s4",
        "title": "🔥 Section 4: 极客社区热议 (Reddit · 共 2 条)",
        "link_label": "查看讨论",
        "total": 2,
        "sources": [{"name": "reddit", "count": 2, "max_days": None, "limit": 6, "timeout_sec": 20}],
    },
]

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "papers": True,                 # 论文源开关（R2）；默认开 = 现行行为
    "dedupe_window_days": 2,        # 去重库窗口（R3）；默认 2 天 = 现行 48h
    "fill_gap": True,               # 顺延补齐（R3 要关掉）；默认开 = 现行行为
    "paths": {
        "seen_file": str(XIAO_PIN_DATA / "seen_trends.json"),
        "snapshot_file": str(XIAO_PIN_DATA / "last_digest_snapshot.json"),
    },
    "x": {                          # queries 为空 = 用 fetcher 默认查询抓一次（旧行为）
        "queries": [],
        "exclude": [],
        "limit_per_query": 5,
        "timeout_sec": 15,
    },
    "sections": LEGACY_SECTIONS,
}


class ConfigError(ValueError):
    """配置缺失/非法。必须 fail fast —— 静默兜底等于换了个口径还假装没事。"""


def _deep_merge(base: Any, override: Any) -> Any:
    """dict 逐键合并；list / 标量整体替换（list 逐项合并会让板块配置半新半旧）。"""
    if isinstance(base, dict) and isinstance(override, dict):
        out = copy.deepcopy(base)
        for k, v in override.items():
            out[k] = _deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(override)


def validate(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """结构校验：板块/信源名字打错、total 非正、源为空都要当场炸。"""
    if not isinstance(cfg.get("sections"), list) or not cfg["sections"]:
        raise ConfigError("sections 不能为空")
    for sec in cfg["sections"]:
        sid = sec.get("id") or "<无 id>"
        if not sec.get("title"):
            raise ConfigError(f"板块 {sid} 缺 title")
        total = sec.get("total")
        if not isinstance(total, int) or total <= 0:
            raise ConfigError(f"板块 {sid} 的 total 必须是正整数，实际 {total!r}")
        srcs = sec.get("sources") or []
        if not srcs:
            raise ConfigError(f"板块 {sid} 没有任何 sources")
        for s in srcs:
            if s.get("name") not in KNOWN_SOURCES:
                raise ConfigError(f"板块 {sid} 的信源 {s.get('name')!r} 不认识（可选 {KNOWN_SOURCES}）")
    days = cfg.get("dedupe_window_days")
    if not isinstance(days, (int, float)) or days <= 0:
        raise ConfigError(f"dedupe_window_days 必须是正数，实际 {days!r}")
    for key in ("seen_file", "snapshot_file"):
        if not (cfg.get("paths") or {}).get(key):
            raise ConfigError(f"paths.{key} 不能为空")
    return cfg


def load(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """读 config/trends.yaml（缺文件则纯默认），深合并到 DEFAULTS 上并校验。"""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = copy.deepcopy(DEFAULTS)
    if p.exists():
        import yaml  # 延迟导入：命令行只查默认值时不必依赖 pyyaml
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{p} 顶层必须是映射，实际 {type(raw).__name__}")
        cfg = _deep_merge(cfg, raw)
    return validate(cfg)


def legacy() -> Dict[str, Any]:
    """迁移前行为（A/B 对照用）：不读文件。"""
    return validate(copy.deepcopy(DEFAULTS))


def missing_path_warnings(cfg: Dict[str, Any]) -> List[str]:
    """去重库/快照所在目录不存在时告警（口径 4：换机器会拿到空库，重复率反而飙升）。"""
    out = []
    for key in ("seen_file", "snapshot_file"):
        p = Path(cfg["paths"][key])
        if not p.parent.exists():
            out.append(f"{key}={p} 的目录不存在：本轮会当成空库/不落快照（换机器请改 config/trends.yaml）")
    return out


if __name__ == "__main__":  # 便于 T0 验收：python -m src.digest.config
    import json
    print(json.dumps(load(), ensure_ascii=False, indent=2))
