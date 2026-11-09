#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.9 配置面收敛（spec_enhancement.md §5 T1.9）

对应缺口 G-B：`.env` 里有死键、代码读了却没声明的键、且启动不 fail-fast。

三条硬规则：
  1. **缺必填键 = 启动即失败**（`MissingConfig`，退出码 78），不允许跑到第 3 步才炸
  2. **默认值只允许出现在非敏感、非必填键上** —— 敏感键一律 `os.getenv(k)` 且缺失即抛
  3. **掩码**：`*_KEY` / `*_SECRET` / `*_TOKEN` / `*_PASSWORD` 在日志与异常里只显示 `ab****yz`

`audit()` 用于 CI / 人工巡检：比对「代码实读键」「pipeline.yaml 声明键」「.env 实际键」，
找出死键（DEADKEY）与未声明键（UNDECLARED）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

PROJ = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJ / ".env"
ENV_EXAMPLE = PROJ / ".env.example"
SCAN_DIRS = (PROJ / "src", PROJ / "scripts")

# 允许把 env 文件指到别处：`AUTOCONTENT_ENV_FILE=/dev/null` 可真实模拟「缺键」场景
# （否则 `env -u LLM_API_KEY` 会被 .env 文件重新补回，测不出 fail-fast）
ENV_FILE_OVERRIDE_VAR = "AUTOCONTENT_ENV_FILE"

EXIT_MISSING_CONFIG = 78

# 静态扫描：os.getenv("K") / os.environ["K"] / os.environ.get("K") / os.getenv('K')
_GETENV_RE = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*['"]([A-Z][A-Z0-9_]*)['"]"""
    r"""|os\.environ\[\s*['"]([A-Z][A-Z0-9_]*)['"]\s*\]"""
)
_MASK_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|COOKIE|CT0)$")


class MissingConfig(RuntimeError):
    """必填配置缺失。退出码 78（EX_CONFIG）。"""

    def __init__(self, missing: Iterable[str], code: int = EXIT_MISSING_CONFIG):
        self.missing = sorted(set(missing))
        self.code = code
        super().__init__("缺失必填环境变量: " + ", ".join(self.missing))


@dataclass
class AuditResult:
    declared: set[str] = field(default_factory=set)
    required: set[str] = field(default_factory=set)
    optional: set[str] = field(default_factory=set)
    env_file: set[str] = field(default_factory=set)
    code_read: set[str] = field(default_factory=set)
    dead_keys: list[str] = field(default_factory=list)
    undeclared: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.dead_keys and not self.undeclared and not self.missing_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "declared": sorted(self.declared),
            "required": sorted(self.required),
            "optional": sorted(self.optional),
            "env_file": sorted(self.env_file),
            "code_read": sorted(self.code_read),
            "DEADKEY": self.dead_keys,
            "UNDECLARED": self.undeclared,
            "MISSING_REQUIRED": self.missing_required,
        }


# ------------------------------------------------------------------ 掩码

def mask(value: str | None, keep: int = 2) -> str:
    """敏感值掩码：`abcdefgh` → `ab****gh`；过短则全掩。"""
    if not value:
        return "(空)"
    if len(value) <= keep * 2:
        return "****"
    return f"{value[:keep]}****{value[-keep:]}"


def is_sensitive(key: str) -> bool:
    return bool(_MASK_RE.search(key.upper()))


# ------------------------------------------------------------------ 环境

def env_file_path() -> Path:
    """当前生效的 env 文件路径（可被 AUTOCONTENT_ENV_FILE 覆盖）。"""
    override = os.getenv(ENV_FILE_OVERRIDE_VAR)
    return Path(override) if override else ENV_FILE


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """极简 .env 解析（KEY=VALUE，支持 # 注释与引号）。不覆盖已存在的 os.environ。"""
    path = path or env_file_path()
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            data[k] = v
            os.environ.setdefault(k, v)
    return data


def _declared(cfg: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """从 pipeline.yaml 取：所有声明键 / 必填键 / 可选键。"""
    providers = cfg.get("providers") or {}
    all_declared: set[str] = set()
    for prov in providers.values():
        for v in (prov.get("env") or {}).values():
            all_declared.add(str(v))

    binding = (cfg.get("contract") or {}).get("config_binding") or {}
    required = {str(k) for k in binding.get("required_env") or []}
    optional = {str(k) for k in binding.get("optional_env") or []}
    # 未列在 required/optional 的 provider 键，默认为 required（声明即必填）
    required |= (all_declared - optional)
    return all_declared | optional, required, optional


def code_read_keys(dirs: Iterable[Path] = SCAN_DIRS) -> set[str]:
    """静态扫描代码里实读的环境变量键（跳过注释行，避免把文档示例当成真实读取）。"""
    found: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            if "_deprecated" in py.parts or "node_modules" in py.parts:
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                code = line.split("#", 1)[0]  # 去掉行尾注释
                for m in _GETENV_RE.finditer(code):
                    key = m.group(1) or m.group(2)
                    if key and key != "K":  # 'K' 是文档示例占位符
                        found.add(key)
    return found


def load_required(cfg: dict[str, Any] | None = None) -> dict[str, str]:
    """校验必填键，缺一即抛 `MissingConfig`。返回 {key: value}（值不打印）。"""
    if cfg is None:
        from src.pipeline.contracts import load_pipeline
        cfg = load_pipeline()
    load_env_file()
    _, required, _ = _declared(cfg)
    missing = [k for k in sorted(required) if not os.getenv(k)]
    if missing:
        raise MissingConfig(missing)
    return {k: os.getenv(k, "") for k in required}


def audit(cfg: dict[str, Any] | None = None) -> AuditResult:
    """三面比对：代码实读 / 声明 / .env 实际。"""
    if cfg is None:
        from src.pipeline.contracts import load_pipeline
        cfg = load_pipeline()
    env_file = load_env_file()
    declared, required, optional = _declared(cfg)
    read = code_read_keys()

    res = AuditResult(
        declared=declared, required=required, optional=optional,
        env_file=set(env_file), code_read=read,
    )
    # 死键：在 .env 里，但代码不读、也不是声明键
    res.dead_keys = sorted(k for k in env_file if k not in read and k not in declared)
    # 未声明：代码要读，但 pipeline.yaml 既没声明为必填也没声明为可选
    res.undeclared = sorted(k for k in read if k not in declared)
    # 必填缺失
    res.missing_required = sorted(k for k in required if not os.getenv(k))
    return res


def masked_snapshot(keys: Iterable[str] | None = None) -> dict[str, str]:
    """给日志/报告用的掩码快照。"""
    env_file = load_env_file()
    picked = list(keys) if keys is not None else sorted(env_file)
    return {k: (mask(env_file.get(k)) if is_sensitive(k) else (env_file.get(k) or "(空)"))
            for k in picked}


__all__ = [
    "MissingConfig", "AuditResult", "EXIT_MISSING_CONFIG",
    "mask", "is_sensitive", "load_env_file", "load_required", "audit",
    "code_read_keys", "masked_snapshot", "ENV_FILE", "ENV_EXAMPLE",
]
