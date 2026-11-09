#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.2 契约校验器（spec_enhancement.md §5 T1.2）

职责：把 `pipeline.yaml` 从"一段能解析的 YAML"变成"一份可判定的契约"。
本模块只做校验，**不执行任何步骤、不调用任何模型**。

五个公开能力：
    load_pipeline(path)                   解析 + schema_version 校验
    validate_structure(cfg)               结构完整性（步骤/hook/槽位/profile）
    assert_no_model_literal(cfg)          禁止出现模型名（G3 模型无关性）
    verify_produces(step, run_root)       产物存在且非空（出厂对账）
    assert_no_caller_max_tokens(paths)    禁止调用方自定 max_tokens（QA#2②）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("需要 PyYAML：pip install pyyaml") from exc

SCHEMA_VERSION = "1.0"

# kind: llm 的 provider 必须声明的六项能力画像（T1.4 填值，但骨架必须在）
LLM_PROFILE_FIELDS: tuple[str, ...] = (
    "max_tokens",
    "reasoning_reserve",
    "timeout_sec",
    "json_mode",
    "min_output_chars",
    "expected_latency_sec",
)

# 每个 step 必须且只能有一种执行体
STEP_EXECUTORS: tuple[str, ...] = ("run", "uses", "type")

# 这些 contract 段是硬要求（v1.1 核心契约），缺一即视为声明失效
REQUIRED_CONTRACTS: tuple[str, ...] = (
    "isolation",
    "model_decoupling",
    "model_switch",
    "silence_tolerance",
    "deduplication",
    "artifact_ownership",
)

# 允许出现 max_tokens 的唯一文件（T1.5 落地后为统一调用层）
MAX_TOKENS_WHITELIST: tuple[str, ...] = ("src/llm/client.py",)

PROJ = Path(__file__).resolve().parent.parent.parent


class ContractError(RuntimeError):
    """契约被违反（不可继续执行）。"""


# ------------------------------------------------------------------ 1. 加载

def load_pipeline(path: str | Path = "pipeline.yaml") -> dict[str, Any]:
    """解析 pipeline.yaml 并校验 schema_version。"""
    p = Path(path)
    if not p.is_absolute():
        p = (PROJ / p) if (PROJ / p).exists() else Path.cwd() / p
    if not p.exists():
        raise ContractError(f"pipeline 文件不存在: {p}")
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ContractError("pipeline 顶层必须是映射")
    sv = str(cfg.get("schema_version", ""))
    if sv != SCHEMA_VERSION:
        raise ContractError(f"schema_version 期望 {SCHEMA_VERSION}，实际 {sv or '(缺失)'}")
    for key in ("pipeline", "contract", "providers", "steps", "hooks"):
        if key not in cfg:
            raise ContractError(f"缺少顶层段: {key}")
    return cfg


# ------------------------------------------------------------------ 2. 结构

def validate_structure(cfg: dict[str, Any], *, check_impls: bool = False) -> list[str]:
    """返回错误列表；空列表 = 结构合法。不抛异常，便于一次性打全所有问题。"""
    errors: list[str] = []
    providers: dict[str, Any] = cfg.get("providers") or {}
    steps: Sequence[dict[str, Any]] = cfg.get("steps") or []
    hooks: dict[str, Any] = cfg.get("hooks") or {}

    # 2.1 contract 必需段
    contract = cfg.get("contract") or {}
    for name in REQUIRED_CONTRACTS:
        if name not in contract:
            errors.append(f"contract.{name} 缺失")

    # 2.2 provider profile
    for name, prov in providers.items():
        kind = (prov or {}).get("kind")
        profile = (prov or {}).get("profile") or {}
        if kind == "llm":
            missing = [f for f in LLM_PROFILE_FIELDS if f not in profile]
            if missing:
                errors.append(f"providers.{name}(llm) profile 缺字段: {', '.join(missing)}")
        elif kind == "tts":
            if "timeout_sec" not in profile:
                errors.append(f"providers.{name}(tts) profile 缺 timeout_sec")
        elif not kind:
            errors.append(f"providers.{name} 缺 kind")

    # 2.3 步骤唯一性 + 执行体互斥
    seen_ids: set[str] = set()
    produced: dict[str, str] = {}
    for idx, step in enumerate(steps):
        sid = (step or {}).get("id")
        if not sid:
            errors.append(f"steps[{idx}] 缺 id")
            continue
        if sid in seen_ids:
            errors.append(f"steps 中 id 重复: {sid}")
        seen_ids.add(sid)

        kinds = [k for k in STEP_EXECUTORS if step.get(k)]
        if len(kinds) != 1:
            errors.append(f"step {sid} 必须且只能有 run/uses/type 之一，实际 {kinds or '无'}")
        if step.get("uses") and step["uses"] not in providers:
            errors.append(f"step {sid} 的 uses={step['uses']} 未在 providers 中声明")
        if not step.get("name"):
            errors.append(f"step {sid} 缺 name")

        # 2.4 hook 引用可解析
        for phase in ("before_hooks", "after_hooks"):
            for h in step.get(phase) or []:
                if h not in hooks:
                    errors.append(f"step {sid}.{phase} 引用未注册的 hook: {h}")
                elif check_impls:
                    impl = (hooks.get(h) or {}).get("impl")
                    if impl and not (PROJ / impl).exists():
                        errors.append(f"hook {h} 的 impl 不存在: {impl}")

        # 2.5 产物唯一归属（同一产物不得由两步声明）
        for art in step.get("produces") or []:
            if art in produced and produced[art] != sid:
                errors.append(f"产物 {art} 被 {produced[art]} 与 {sid} 重复声明")
            produced[art] = sid

    # 2.6 reads 必须能溯源到某个 step 的 produces（或 inputs/manifest）
    upstream = set(produced) | {"manifest.json"}
    for step in steps:
        for art in step.get("reads") or []:
            if art not in upstream:
                errors.append(f"step {step.get('id')} 的 reads={art} 无上游产出")

    # 2.7 注册但未引用的 hook（视为声明白写，v1.2 起按错误处理）
    used: set[str] = set()
    for step in steps:
        used |= set(step.get("before_hooks") or []) | set(step.get("after_hooks") or [])
    for h in hooks:
        if h not in used:
            errors.append(f"hook {h} 已注册但未被任何 step 引用")

    return errors


def collect_warnings(cfg: dict[str, Any]) -> list[str]:
    """非阻断提示（不影响 GATE 通过）。"""
    warns: list[str] = []
    hooks = cfg.get("hooks") or {}
    for name, spec in hooks.items():
        impl = (spec or {}).get("impl")
        if impl and not (PROJ / impl).exists():
            warns.append(f"hook {name} 的 impl 尚未实现: {impl}（T2.1/T2.5 落地）")
    for name, prov in (cfg.get("providers") or {}).items():
        if prov.get("kind") == "llm":
            prof = prov.get("profile") or {}
            if prof.get("max_tokens") is not None and prof.get("reasoning_reserve") is not None:
                if prof["max_tokens"] <= prof["reasoning_reserve"]:
                    warns.append(
                        f"providers.{name} max_tokens({prof['max_tokens']}) "
                        f"<= reasoning_reserve({prof['reasoning_reserve']})，正文预算为 0"
                    )
    return warns


# ------------------------------------------------------------------ 3. 模型名

def _iter_body_parts(cfg: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """遍历需要检查模型名的位置（排除 contract.model_decoupling.forbidden_literals 自身）。
    env 段只允许是键名，所以也纳入检查（键名不含具体模型）。"""
    for i, step in enumerate(cfg.get("steps") or []):
        yield f"steps[{i}:{step.get('id')}]", str(step)
    for name, spec in (cfg.get("hooks") or {}).items():
        yield f"hooks.{name}", str(spec)
    for name, prov in (cfg.get("providers") or {}).items():
        body = {k: v for k, v in prov.items() if k != "env"}
        yield f"providers.{name}", str(body)


def assert_no_model_literal(cfg: dict[str, Any]) -> None:
    """G3：流水线文件内不出现任何模型名。违反即抛 ContractError。"""
    literals = (cfg.get("contract") or {}).get("model_decoupling", {}).get("forbidden_literals") or []
    if not literals:
        return
    hits: list[str] = []
    for where, body in _iter_body_parts(cfg):
        low = body.lower()
        for lit in literals:
            if lit.lower() in low:
                hits.append(f"{where} 含模型字面量 '{lit}'")
    if hits:
        raise ContractError("模型名泄漏（contract.model_decoupling）：\n  " + "\n  ".join(hits))


# ------------------------------------------------------------------ 4. 产物

def verify_produces(step: dict[str, Any], run_root: str | Path) -> list[str]:
    """逐个校验该步骤声明产物存在且非空。返回缺失/空产物的描述列表。"""
    root = Path(run_root)
    missing: list[str] = []
    for art in step.get("produces") or []:
        p = root / art
        if not p.exists():
            missing.append(f"{step.get('id')}: 缺产物 {art}")
        elif p.is_dir():
            if not any(p.iterdir()):
                missing.append(f"{step.get('id')}: 产物目录为空 {art}")
        elif p.stat().st_size == 0:
            missing.append(f"{step.get('id')}: 产物为空文件 {art}")
    return missing


# ------------------------------------------------------------------ 5. max_tokens

def assert_no_caller_max_tokens(
    paths: Sequence[str | Path] | None = None,
    whitelist: Sequence[str] = MAX_TOKENS_WHITELIST,
) -> list[str]:
    """静态扫描代码中是否出现 `max_tokens=` 调用参数（QA#2②）。
    返回违例列表 `file:line: 片段`；空列表 = 合规。
    白名单文件按相对项目根的 POSIX 路径比对。

    用 AST 抓 `call(keyword=max_tokens)`，不做文本匹配 —— 避免把注释/文档串里
    提到 `max_tokens=` 的说明文字误判为违例。
    """
    import ast

    roots = [Path(p) for p in (paths or [PROJ / "src", PROJ / "scripts"])]
    allowed = {PROJ / w for w in whitelist}
    violations: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if "__pycache__" in py.parts or py in allowed:
                continue
            try:
                src = py.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            lines = src.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not any(kw.arg == "max_tokens" for kw in node.keywords):
                    continue
                no = getattr(node, "lineno", 0)
                snippet = lines[no - 1].strip()[:100] if 0 < no <= len(lines) else ""
                rel = py.relative_to(PROJ) if py.is_relative_to(PROJ) else py
                violations.append(f"{rel}:{no}: {snippet}")
    return sorted(set(violations))


__all__ = [
    "SCHEMA_VERSION",
    "LLM_PROFILE_FIELDS",
    "ContractError",
    "load_pipeline",
    "validate_structure",
    "collect_warnings",
    "assert_no_model_literal",
    "verify_produces",
    "assert_no_caller_max_tokens",
]
