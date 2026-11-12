#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.7 物料契约加载与校验（spec_enhancement.md §5 T1.7）

设计要点：
  · **`manifest.schema.json` 是唯一事实来源**，本模块不复制规则，而是读取 schema
    并实现它实际用到的关键字子集（required / anyOf / type / min/maxLength /
    pattern / maxItems / items / enum / properties）。
  · 不引入 `jsonschema` 第三方依赖（环境里没有，也不该为一份 5 字段契约加包）。
  · `validate()` 返回错误列表，空列表 = 通过；调用方（check_material 步）据此 block，
    **绝不"猜一个值糊过去"**。

用法：
    from src.pipeline.manifest import load_manifest, validate
    m = load_manifest("/path/to/manifest.json")   # 也接受 dict 或 "-"（stdin）
    errors = validate(m)                           # [] 即通过
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = PROJ / "manifest.schema.json"

_SCHEMA_CACHE: dict[str, Any] | None = None


def schema() -> dict[str, Any]:
    """加载（并缓存）schema 文件。"""
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"物料契约缺失: {SCHEMA_PATH}（T1.7 交付物）")
        loaded: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        _SCHEMA_CACHE = loaded
        return loaded
    return _SCHEMA_CACHE


# ------------------------------------------------------------------ 数据模型

@dataclass
class Manifest:
    title: str
    body: str | None = None
    source_url: str | None = None
    account: str | None = None
    images: list[str] = field(default_factory=list)
    task_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """把整份物料打包成提示块（供 hook.material_bundle / expand_script 使用）。
        不做任何"预拆分"：只声明事实来源，字段由模型扩写。"""
        parts = [f"<material>\n<title>{self.title}</title>"]
        if self.body:
            parts.append(f"<body>\n{self.body}\n</body>")
        if self.source_url:
            parts.append(f"<source_url>{self.source_url}</source_url>")
        if self.images:
            parts.append("<images>\n" + "\n".join(f"- {u}" for u in self.images) + "\n</images>")
        parts.append("</material>")
        parts.append(
            "【硬约束】只能基于上述物料扩写，严禁引入物料中不存在的事实、数字或结论；"
            "物料未提供的信息一律留空或省略，不得编造。"
        )
        return "\n".join(parts)


# ------------------------------------------------------------------ 加载

def load_manifest(src: str | Path | dict[str, Any]) -> Manifest:
    """从文件路径 / dict / '-'（stdin）加载。只做解析，不做校验。"""
    if isinstance(src, dict):
        data = src
    elif str(src) == "-":
        data = json.load(sys.stdin)
    else:
        p = Path(src)
        if not p.exists():
            raise FileNotFoundError(f"物料文件不存在: {p}")
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"物料文件为空: {p}")
        data = json.loads(text)

    if not isinstance(data, dict):
        raise ValueError(f"物料必须是 JSON object，实际 {type(data).__name__}")

    images = data.get("images") or []
    return Manifest(
        title=str(data.get("title") or "").strip(),
        body=(str(data["body"]).strip() if data.get("body") else None),
        source_url=(str(data["source_url"]).strip() if data.get("source_url") else None),
        account=(str(data["account"]).strip() if data.get("account") else None),
        images=[str(x) for x in images] if isinstance(images, list) else [],
        task_id=(str(data["task_id"]).strip() if data.get("task_id") else None),
        raw=data,
    )


# ------------------------------------------------------------------ 校验（schema 驱动）

_TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _check_type(value: Any, want: str) -> bool:
    py = _TYPE_MAP.get(want)
    if py is None:
        return True
    if want in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, py)


def _validate_node(data: dict[str, Any], spec: dict[str, Any], path: str = "") -> list[str]:
    """递归校验（只覆盖 schema 里实际用到的关键字子集）。"""
    errors: list[str] = []
    where = path or "(根)"

    for key in spec.get("required") or []:
        if key not in data or data[key] in (None, ""):
            errors.append(f"{where}: 缺必填字段 {key}")

    branches = spec.get("anyOf") or []
    if branches:
        # anyOf：只要有一个分支通过即视为满足；全部分支失败才报错
        if all(_validate_node(data, b, path) for b in branches):
            reqs = [", ".join(b.get("required") or []) or "子模式" for b in branches]
            errors.append(f"{where}: 需至少满足其一 → {' 或 '.join(reqs)}")

    props = spec.get("properties") or {}
    for name, rule in props.items():
        if name not in data or data[name] is None:
            continue
        value = data[name]
        p = f"{path}.{name}" if path else name
        want = rule.get("type")
        if want and not _check_type(value, want):
            errors.append(f"{p}: 类型应为 {want}，实际 {type(value).__name__}")
            continue
        if isinstance(value, str):
            if "minLength" in rule and len(value) < rule["minLength"]:
                errors.append(f"{p}: 长度 {len(value)} < minLength {rule['minLength']}")
            if "maxLength" in rule and len(value) > rule["maxLength"]:
                errors.append(f"{p}: 长度 {len(value)} > maxLength {rule['maxLength']}")
            if "pattern" in rule and not re.search(rule["pattern"], value):
                errors.append(f"{p}: 不匹配模式 {rule['pattern']}")
        if isinstance(value, list):
            if "maxItems" in rule and len(value) > rule["maxItems"]:
                errors.append(f"{p}: 元素数 {len(value)} > maxItems {rule['maxItems']}")
            if "minItems" in rule and len(value) < rule["minItems"]:
                errors.append(f"{p}: 元素数 {len(value)} < minItems {rule['minItems']}")
            item_rule = rule.get("items")
            if isinstance(item_rule, dict):
                for i, item in enumerate(value):
                    ip = f"{p}[{i}]"
                    if item_rule.get("type") and not _check_type(item, item_rule["type"]):
                        errors.append(f"{ip}: 类型应为 {item_rule['type']}，实际 {type(item).__name__}")
                        continue
                    if isinstance(item, str) and "minLength" in item_rule and len(item) < item_rule["minLength"]:
                        errors.append(f"{ip}: 长度 {len(item)} < minLength {item_rule['minLength']}")
        if "enum" in rule and value not in rule["enum"]:
            errors.append(f"{p}: 取值应为 {rule['enum']} 之一")
    return errors


# ------------------------------------------------------------------ 课文故事（textbook_story）专属校验
# F-2026-11-12-TEXTBOOK-STORY §5 数据契约 + §8.2 验收：facts 缺 source 必须报错。
# JSON-schema 子集表达不了「每条 fact 的 source 非空」「facts 条数下限」这类跨元素规则，
# 所以在这里用代码补位（schema 仍是结构 + 类型的事实来源）。

CONTENT_TYPE_DEFAULT = "tech_news"
CONTENT_TYPE_TEXTBOOK = "textbook_story"
TEXTBOOK_MIN_FACTS = 3
TEXTBOOK_REQUIRED_TEXTBOOK_FIELDS = (("poem_title", "诗题"), ("dynasty", "朝代"), ("author", "作者"))
DEFAULT_TEXTBOOK_DURATION_SEC = 120
TEXTBOOK_DURATION_RANGE = (30, 600)


def content_type_of(data: dict[str, Any] | None) -> str:
    """物料品类；缺省按科技速报处理（现有物料不带该字段，行为不变）。"""
    if not isinstance(data, dict):
        return CONTENT_TYPE_DEFAULT
    ct = str(data.get("content_type") or "").strip()
    return ct or CONTENT_TYPE_DEFAULT


def validate_textbook_story(data: dict[str, Any]) -> list[str]:
    """课文故事物料的语义校验。非 textbook_story → 返回空列表（科技链路零影响）。

    规则（spec §5）：
      · textbook / facts 必填
      · facts ≥ 3 条，每条 claim 与 source 均非空（缺出处 = 不可核查，禁止进渲染）
      · textbook.poem_title / dynasty / author 非空
      · storyboard_hint.target_duration_sec 若给出，必须是合理整数秒
    """
    if content_type_of(data) != CONTENT_TYPE_TEXTBOOK:
        return []

    errors: list[str] = []

    tb = data.get("textbook")
    if not isinstance(tb, dict) or not tb:
        errors.append("textbook: 缺失或不是 object（课文故事必须声明诗题/朝代/作者/册次）")
    else:
        for field_name, label in TEXTBOOK_REQUIRED_TEXTBOOK_FIELDS:
            if not str(tb.get(field_name) or "").strip():
                errors.append(f"textbook.{field_name}: {label}为空（课文故事必填）")

    facts = data.get("facts")
    if not isinstance(facts, list) or not facts:
        errors.append("facts: 缺失或不是 array（课文故事必须提供可核查事实卡）")
    else:
        if len(facts) < TEXTBOOK_MIN_FACTS:
            errors.append(f"facts: 只有 {len(facts)} 条，至少需要 {TEXTBOOK_MIN_FACTS} 条带出处的事实")
        for i, fact in enumerate(facts):
            if not isinstance(fact, dict):
                errors.append(f"facts[{i}]: 不是 object")
                continue
            if not str(fact.get("claim") or "").strip():
                errors.append(f"facts[{i}].claim: 为空（事实必须有明确表述）")
            if not str(fact.get("source") or "").strip():
                errors.append(
                    f"facts[{i}].source: 为空 —— 史实缺出处不可核查，"
                    f"物料校验失败，不允许进入渲染（spec §5）")

    hint = data.get("storyboard_hint")
    if hint is not None:
        if not isinstance(hint, dict):
            errors.append("storyboard_hint: 必须是 object")
        else:
            target = hint.get("target_duration_sec")
            if target is not None:
                lo, hi = TEXTBOOK_DURATION_RANGE
                try:
                    sec = int(target)
                except (TypeError, ValueError):
                    errors.append(f"storyboard_hint.target_duration_sec: 不是整数（{target!r}）")
                else:
                    if not lo <= sec <= hi:
                        errors.append(
                            f"storyboard_hint.target_duration_sec: {sec} 秒超出合理区间 {lo}~{hi}")
    return errors


def target_duration_sec(data: dict[str, Any] | None) -> int:
    """课文故事目标时长；物料没写就用缺省值（prompt 与出片断言共用同一口径）。"""
    if isinstance(data, dict):
        hint = data.get("storyboard_hint")
        if isinstance(hint, dict):
            try:
                sec = int(hint.get("target_duration_sec") or 0)
            except (TypeError, ValueError):
                sec = 0
            if sec > 0:
                return sec
    return DEFAULT_TEXTBOOK_DURATION_SEC


def validate(manifest: Manifest | dict[str, Any]) -> list[str]:
    """按 manifest.schema.json 校验 + 品类专属语义规则，返回错误列表（空 = 通过）。"""
    data = manifest.raw if isinstance(manifest, Manifest) else manifest
    if not isinstance(data, dict):
        return [f"物料必须是 JSON object，实际 {type(data).__name__}"]
    return _validate_node(data, schema()) + validate_textbook_story(data)


def require_valid(manifest: Manifest | dict[str, Any]) -> Manifest:
    """校验不通过即抛 ValueError（check_material 步 block 用）。"""
    errors = validate(manifest)
    if errors:
        raise ValueError("物料不符合 manifest.schema.json：\n  - " + "\n  - ".join(errors))
    return manifest if isinstance(manifest, Manifest) else load_manifest(manifest)


__all__ = [
    "Manifest", "schema", "load_manifest", "validate", "require_valid", "SCHEMA_PATH",
    "CONTENT_TYPE_DEFAULT", "CONTENT_TYPE_TEXTBOOK", "TEXTBOOK_MIN_FACTS",
    "content_type_of", "validate_textbook_story", "target_duration_sec",
    "DEFAULT_TEXTBOOK_DURATION_SEC",
]
