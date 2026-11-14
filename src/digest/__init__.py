"""科技速报编排（SPEC-20261114 · R5）。

信息源抓取仍在 `src.crawlers.atomic_fetchers`（流水线内，单一实现）；
本包只负责「编排 + 去重 + 板块 + 快照」，从 xiao-pin 的 `scripts/fetch_trends.py` 迁入，
xiao-pin 侧保留薄壳转发。
"""
from .config import ConfigError, DEFAULTS, load, legacy  # noqa: F401

__all__ = ["ConfigError", "DEFAULTS", "load", "legacy"]
