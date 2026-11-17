"""creator_take（分镜二次创作）门禁：不够独创就不许出片。

为什么需要
----------
模板能渲染锐评，不等于脚本里有真锐评。历史物料 4/4 分镜没有 creator_take，
口播全是事实复述——用户明明要「往外扩」，结果出片是搬运。所以把它做成硬门禁：
不达标直接 BLOCKED，而不是渲染完再说。

判据（任一不满足 → 失败）
  1. 完整性：每个分镜必须有 creator_take
  2. 长度：15~60 字（太短是口号，太长画面放不下）
  3. 不抄：与 segment_narration + fact_summary_150 的 4-gram 重合率 < 40%
  4. 非空话：不许是「值得关注」「意义重大」「引发热议」这类无信息短语
  5. 合规：禁绝对化（最好/最强/唯一…）、禁煽动（震惊/炸了/封杀/内幕…）、禁站外引流

用法
----
    from src.utils.creator_take_guard import check_creator_takes
    errors = check_creator_takes(draft["scenes"], draft.get("fact_summary_150", ""))
    if errors:
        raise ...   # 门禁不通过
"""
from __future__ import annotations

import re

MAX_OVERLAP = 0.40
MIN_LEN = 15
MAX_LEN = 60

EMPTY_PHRASES = (
    "值得关注", "意义重大", "引发热议", "引人深思", "影响深远", "值得期待",
    "拭目以待", "不容忽视", "值得思考", "我们拭目",
)

BANNED = {
    "绝对化": ("最好", "最强", "第一", "唯一", "绝对", "百分百", "史上", "顶级", "最高级", "最佳", "最优"),
    "煽动": ("震惊", "炸了", "封杀", "内幕", "重磅", "突发", "崩了", "完蛋", "彻底凉"),
    "引流": ("加V", "加v", "私信我", "公众号", "二维码", "主页置顶", "后台回复", "领取源码", "求源码"),
}


def _clean(text: str) -> str:
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")


def _grams(text: str, n: int = 4) -> set[str]:
    t = _clean(text)
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def overlap_ratio(take: str, source: str) -> float:
    """take 的 4-gram 有多大比例出现在 source 里（0~1）。"""
    g = _grams(take)
    if not g:
        return 0.0
    return len(g & _grams(source)) / len(g)


def check_creator_takes(scenes: list, fact_summary: str = "") -> list[str]:
    """返回错误列表；空列表代表通过。"""
    errors: list[str] = []
    if not scenes:
        return ["scenes 为空，无法校验 creator_take"]

    fact_source = fact_summary or ""

    for i, scene in enumerate(scenes):
        idx = scene.get("scene_index", i) if isinstance(scene, dict) else i
        take = (scene.get("creator_take") or "").strip() if isinstance(scene, dict) else ""
        narration = scene.get("segment_narration", "") if isinstance(scene, dict) else ""

        if not take:
            errors.append(f"[scene {idx}] 缺 creator_take（分镜没有二次创作，等于搬运）")
            continue

        if not (MIN_LEN <= len(take) <= MAX_LEN):
            errors.append(f"[scene {idx}] creator_take 长度 {len(take)} 字，要求 {MIN_LEN}~{MAX_LEN}")

        source = f"{narration} {fact_source}"
        ratio = overlap_ratio(take, source)
        if ratio >= MAX_OVERLAP:
            errors.append(
                f"[scene {idx}] creator_take 与原文 4-gram 重合 {ratio:.0%} ≥ {MAX_OVERLAP:.0%}（在复述，不是在扩展）")

        for phrase in EMPTY_PHRASES:
            if phrase in take:
                errors.append(f"[scene {idx}] creator_take 含空话「{phrase}」，没有信息量")

        for kind, words in BANNED.items():
            hit = [w for w in words if w in take]
            if hit:
                errors.append(f"[scene {idx}] creator_take 含{kind}词 {hit}")

    return errors


def summarize(scenes: list, fact_summary: str = "") -> dict:
    """给审计/日志用的统计（不判错）。"""
    takes = [((s.get("creator_take") or "") if isinstance(s, dict) else "") for s in scenes]
    filled = [t for t in takes if t.strip()]
    ratios = [
        overlap_ratio(t, f"{s.get('segment_narration','')} {fact_summary}")
        for t, s in zip(takes, scenes) if t.strip() and isinstance(s, dict)
    ]
    return {
        "scenes": len(scenes),
        "with_take": len(filled),
        "avg_overlap": round(sum(ratios) / len(ratios), 3) if ratios else None,
        "max_overlap": round(max(ratios), 3) if ratios else None,
    }
