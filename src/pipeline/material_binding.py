"""物料硬约束（A 硬绑定 + B fail-closed 校验 + C 审计留痕）。

为什么要有（2026-11-15，ai-study-survey-1 真机事故）：
    `material.tags` 从来没进过生成环节 —— `exec_expand_script` 只把 `title + body` 拼成
    `news_text` 喂给 LLM，tags 全由模型自由发挥。结果是物料里的「学习方法」被模型用
    「AI依赖」顶掉；更狠的一次整组 tags 直接被换掉（`vibe知识大赏`/`ai学习工具` 全丢）。
    `title` 同理：LLM 会重写，物料里的品牌前缀就这么没了。

现在的口径：
    · **物料优先，生成只能补位**：`title = 物料 title`（物料非空时）；
      `tags = dedupe(物料 tags + 生成 tags)[:MAX_TAGS]`，物料在前、按序保留；
    · **超限不静默**：物料 title > TITLE_MAX 字符、物料 tags > MAX_TAGS 个 → 直接报错，
      要求物料侧改短/裁剪，绝不在运行时静默截断（截断比报错更难查）；
    · **生成后自证（fail-closed）**：`物料 tags ⊄ 产物 tags` → `TAGS_DROPPED`；
      `产物 title != 物料 title` → `TITLE_REWRITTEN`；
    · **审计留痕**：`output/<日期>/<run>/material_binding.json` 记「物料原值 / 生成原值 /
      绑定后 / 校验结论」，出事后能一眼看出是谁改的。

用法：
    from src.pipeline.material_binding import apply_binding, MaterialBindingError

    bound, audit_rec = apply_binding(material, generated)   # 违反约束 → MaterialBindingError
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

#: 一条视频最多带几个话题（抖音/视频号都不宜堆太多，物料全保留、生成只填空位）
MAX_TAGS = 6

#: 与 `src.schemas.DraftScriptPayload.title` 的 max_length 保持一致（那里会静默截断，
#: 所以必须在这里先拦下来，否则「物料标题被改」根本查不出来）
TITLE_MAX = 20


class MaterialBindingError(Exception):
    """物料与生成结果对不上（fail-closed：宁可停住，也不发错文案）。"""

    def __init__(self, codes: List[str], detail: str = "") -> None:
        self.codes = codes
        super().__init__(f"{'、'.join(codes)}{('：' + detail) if detail else ''}")


def dedupe(items: Iterable[Any]) -> List[str]:
    """按原序去重、去空白（字符串比较，不区分大小写以外的处理 —— 话题就是原样文本）。"""
    out: List[str] = []
    seen: set[str] = set()
    for raw in items or []:
        key = str(raw or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def bind_tags(material_tags: Iterable[Any], generated_tags: Iterable[Any],
              limit: int = MAX_TAGS) -> List[str]:
    """物料 tags 全保留在前，生成 tags 只填空位。"""
    return dedupe(list(material_tags or []) + list(generated_tags or []))[:limit]


def check_material(material: Dict[str, Any]) -> List[str]:
    """物料自身的硬约束（必须在绑定前判：schema 会静默截断 title，截断后就查不出了）。"""
    codes: List[str] = []
    title = str(material.get("title") or "").strip()
    if len(title) > TITLE_MAX:
        codes.append(f"TITLE_TOO_LONG({len(title)}>{TITLE_MAX})")
    tags = dedupe(material.get("tags") or [])
    if len(tags) > MAX_TAGS:
        codes.append(f"TAGS_OVER_LIMIT({len(tags)}>{MAX_TAGS})")
    return codes


def apply_binding(material: Dict[str, Any], generated: Dict[str, Any],
                  limit: int = MAX_TAGS) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """硬绑定：返回 (绑定后的 payload dict, 审计记录)。

    违反物料约束 → `MaterialBindingError`（不返回半成品）。
    """
    material = dict(material or {})
    generated = dict(generated or {})

    codes = check_material(material)
    if codes:
        raise MaterialBindingError(
            codes,
            "物料超限必须由物料侧改短/裁剪，不在运行时静默截断"
            f"（当前 title={str(material.get('title') or '')!r}，"
            f"tags={dedupe(material.get('tags') or [])}）")

    m_title = str(material.get("title") or "").strip()
    m_tags = dedupe(material.get("tags") or [])
    g_title = str(generated.get("title") or "").strip()
    g_tags = dedupe(generated.get("tags") or [])

    bound = dict(generated)
    if m_title:
        bound["title"] = m_title
    bound["tags"] = bind_tags(m_tags, g_tags, limit=limit)

    report = {
        "version": 1,
        "limits": {"max_tags": limit, "title_max": TITLE_MAX},
        "material": {"title": m_title, "tags": m_tags},
        "generated": {"title": g_title, "tags": g_tags},
        "bound": {"title": bound.get("title", ""), "tags": bound["tags"]},
        "dropped_generated": [t for t in g_tags if t not in bound["tags"]],
        "title_source": "material" if m_title else "generated",
        "changed": {"title": bool(m_title and m_title != g_title),
                    "tags": bound["tags"] != g_tags},
        "checks": [],
        "conclusion": "PASS",
    }
    return bound, report


def validate_bound(material: Dict[str, Any], final: Dict[str, Any]) -> List[str]:
    """生成结果自证：物料里的东西一项都不许少（在 schema 校验之后再跑一遍）。"""
    codes: List[str] = []
    m_title = str(material.get("title") or "").strip()
    m_tags = dedupe(material.get("tags") or [])
    f_title = str(final.get("title") or "").strip()
    f_tags = dedupe(final.get("tags") or [])

    if m_title and f_title != m_title:
        codes.append("TITLE_REWRITTEN")
    missing = [t for t in m_tags if t not in f_tags]
    if missing:
        codes.append(f"TAGS_DROPPED({missing})")
    if len(f_tags) > MAX_TAGS:
        codes.append(f"TAGS_OVER_LIMIT({len(f_tags)}>{MAX_TAGS})")
    return codes


def finish_report(report: Dict[str, Any], final: Dict[str, Any],
                  codes: List[str]) -> Dict[str, Any]:
    """把自证结论写进审计记录（生成结果以 schema 校验后的为准）。"""
    out = dict(report)
    out["final"] = {"title": final.get("title", ""), "tags": dedupe(final.get("tags") or [])}
    out["checks"] = codes
    out["conclusion"] = "PASS" if not codes else "FAIL"
    return out
