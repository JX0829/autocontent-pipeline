#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.5 LLM 统一调用层（spec_enhancement.md §5 T1.5）

设计原则（每一条都对应一次真实事故）：
  1. **模型与预算只来自 `pipeline.yaml` 槽位** —— 调用方不许自定 `max_tokens`，
     传了就 `TypeError`（QA#2②：分类 1024 被 reasoning 吃满 → 全判 D）
  2. **四种失败必须抛出，不许返回默认值** —— 截断 / 空返回 / 过短 / 协议错
     （QA#2② #9：静默兜底让故障潜伏数天）
  3. **任何失败先留痕再抛** —— `data/degraded_events.jsonl` + 有表则写 `degraded_events`
     （§4.5 静默失败零容忍）
  4. **不做模型名兜底** —— 缺 `LLM_MODEL_*` 直接失败，绝不再回落到某个写死的老模型
     （T0.2 实测：`comment_funnel` 原默认值是已失效的 `gemini-3.8-flash`）

用法：
    from src.llm.client import LLMClient
    out = LLMClient("llm.fast").complete(
        [{"role": "user", "content": prompt}], temperature=0.1, task="comment_classify")
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src.pipeline.contracts import load_pipeline

logger = logging.getLogger("LLMClient")

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_PIPELINE = PROJ / "pipeline.yaml"
DEGRADED_LOG = PROJ / "data" / "degraded_events.jsonl"


# ------------------------------------------------------------------ 异常族

class LLMError(RuntimeError):
    """调用层统一异常基类。"""


class ConfigError(LLMError):
    """槽位/环境变量配置不完整（缺键即失败，不回落到默认模型）。"""


class TruncatedOutput(LLMError):
    """finish_reason == 'length'：输出被 max_tokens 截断。"""

    def __init__(self, completion_tokens: int, max_tokens: int, slot: str):
        self.completion_tokens = completion_tokens
        self.max_tokens = max_tokens
        self.slot = slot
        super().__init__(
            f"[{slot}] 输出被截断：finish_reason=length，"
            f"completion_tokens={completion_tokens} / max_tokens={max_tokens}（reasoning 吃满预算，回 T1.4 上调）"
        )


class EmptyCompletion(LLMError):
    """content 为空或纯空白。"""


class OutputTooShort(LLMError):
    """可见输出长度小于 profile.min_output_chars。"""

    def __init__(self, got: int, need: int, slot: str):
        super().__init__(f"[{slot}] 输出过短：{got} < min_output_chars {need}")
        self.got, self.need = got, need


class ClassificationError(LLMError):
    """分类结果不合法（未取得 A/B/C/D/E）。替代原「异常即判 D」的静默兜底。"""


# ------------------------------------------------------------------ 用量

@dataclass
class Usage:
    slot: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_sec: float = 0.0
    finish_reason: str = ""

    @property
    def visible_tokens(self) -> int:
        return max(0, self.completion_tokens - self.reasoning_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "visible_tokens": self.visible_tokens,
            "latency_sec": round(self.latency_sec, 2),
            "finish_reason": self.finish_reason,
        }


# ------------------------------------------------------------------ 留痕

def record_degraded(event: str, detail: dict[str, Any], db_path: Path | None = None) -> None:
    """降级留痕：JSONL 必写；`degraded_events` 表存在则同时写库。永不抛异常。"""
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **detail}
    try:
        DEGRADED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DEGRADED_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # 留痕失败也不能影响主流程，但必须吼一声
        logger.error(f"[degraded] 写 JSONL 失败: {exc}")

    db = db_path or (PROJ / "data" / "pipeline_state.db")
    if not db.exists():
        return
    try:
        conn = sqlite3.connect(str(db), timeout=3)
        try:
            has = conn.execute(
                "select name from sqlite_master where type='table' and name='degraded_events'"
            ).fetchone()
            if has:
                conn.execute(
                    "insert into degraded_events (ts, event, detail) values (?,?,?)",
                    (entry["ts"], event, json.dumps(detail, ensure_ascii=False)),
                )
                conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # T2.2 建表前静默；JSONL 已兜底


# ------------------------------------------------------------------ 客户端

def _is_transient(exc: BaseException) -> bool:
    """可重试错误：超时 / 连接失败 / 429 / 5xx。

    鉴权、参数错误重试一万次也没用，只会浪费时间和配额，所以不重试。
    """
    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if any(k in name for k in ("timeout", "connection", "ratelimit", "apiconnection")):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    try:
        status = int(status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return status == 429 or 500 <= status < 600


class LLMClient:
    """按 provider 槽位调用 LLM。预算/超时/JSON 模式全部来自 pipeline.yaml。"""

    def __init__(self, slot: str, cfg: dict[str, Any] | None = None,
                 pipeline_path: str | Path | None = None,
                 *, client: Any = None):
        self.pipeline = cfg or load_pipeline(pipeline_path or DEFAULT_PIPELINE)
        providers = self.pipeline.get("providers") or {}
        if slot not in providers:
            raise ConfigError(f"provider 槽位不存在: {slot}（可选: {', '.join(providers)}）")
        self.slot = slot
        self.provider = providers[slot]
        if self.provider.get("kind") != "llm":
            raise ConfigError(f"{slot} 的 kind={self.provider.get('kind')}，不是 llm 槽位")
        self.profile = self.provider.get("profile") or {}
        for need in ("max_tokens", "reasoning_reserve", "timeout_sec", "min_output_chars"):
            if need not in self.profile:
                raise ConfigError(f"{slot}.profile 缺 {need}（T1.4 标定值必须存在）")

        # env 映射：配置只写键名，值全部来自环境（.env 幂等加载，便于独立调用）
        try:
            from dotenv import load_dotenv
            load_dotenv(PROJ / ".env")
        except ImportError:  # pragma: no cover
            pass
        env_map = self.provider.get("env") or {}
        missing = [k for k in ("base_url", "api_key", "model") if not os.getenv(str(env_map.get(k, "")))]
        if missing:
            keys = [str(env_map.get(k)) for k in missing]
            raise ConfigError(f"{slot} 缺少环境变量: {', '.join(keys)}（禁止用默认模型兜底）")
        self.base_url = os.environ[str(env_map["base_url"])]
        self.api_key = os.environ[str(env_map["api_key"])]
        self.model = os.environ[str(env_map["model"])]

        self._client = client  # 注入点（测试用）
        self.last_usage: Usage | None = None
        self.usage_history: list[Usage] = []

    # ---------------- 内部
    def _openai(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                # 不要只说「未安装」：真正的 ImportError 常来自子依赖不兼容或
                # 解释器选错（conda vs venv），必须把原始异常带出来，否则误诊。
                raise ConfigError(
                    f"openai 不可用（解释器 {sys.executable}）：{type(exc).__name__}: {exc}"
                ) from exc
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=float(self.profile["timeout_sec"]),
            )
        return self._client

    def _record(self, usage: Usage, status: str, extra: dict[str, Any] | None = None) -> None:
        self.last_usage = usage
        self.usage_history.append(usage)
        if status != "OK":
            record_degraded(status, {**usage.as_dict(), **(extra or {})})

    # ---------------- 公开
    def complete(self, messages: Sequence[dict[str, str]], *,
                 temperature: float = 0.3,
                 response_format: dict[str, Any] | None = None,
                 extra_headers: dict[str, str] | None = None,
                 task: str = "", **kwargs: Any) -> str:
        """调用并返回可见文本。失败即抛，绝不返回兜底值。"""
        if "max_tokens" in kwargs:
            raise TypeError(
                f"[{self.slot}] 调用方禁止自定 max_tokens；预算只能来自 "
                f"pipeline.yaml providers.{self.slot}.profile（QA#2②）"
            )

        rf = response_format
        if rf is None and self.profile.get("json_mode"):
            rf = {"type": "json_object"}

        params: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": int(self.profile["max_tokens"]),
        }
        if rf:
            params["response_format"] = rf
        if extra_headers:
            params["extra_headers"] = extra_headers

        usage = Usage(slot=self.slot, model=self.model)
        # 重试预算同样来自 pipeline.yaml 的 profile.retry（默认 1 次 = 不重试）
        rcfg = self.profile.get("retry") or {}
        attempts = max(1, int(rcfg.get("max_attempts") or 1))
        backoff = float(rcfg.get("backoff_sec") or 0)
        t0 = time.time()
        for attempt in range(1, attempts + 1):
            try:
                resp = self._openai().chat.completions.create(**params)
                break
            except Exception as exc:  # 网络/鉴权/超时 —— 留痕后按策略重试或上抛
                usage.latency_sec = time.time() - t0
                transient = _is_transient(exc)
                if attempt < attempts and transient:
                    self._record(usage, "llm_call_retry", {
                        "task": task, "attempt": attempt, "max_attempts": attempts,
                        "error": f"{type(exc).__name__}: {exc}"[:300]})
                    wait = backoff * attempt
                    print(f"      · LLM {type(exc).__name__}（第 {attempt}/{attempts} 次）"
                          f"{wait:.1f}s 后重试")
                    if wait > 0:
                        time.sleep(wait)
                    continue
                self._record(usage, "llm_call_failed", {
                    "task": task, "attempt": attempt, "max_attempts": attempts,
                    "transient": transient,
                    "error": f"{type(exc).__name__}: {exc}"[:300]})
                raise

        usage.latency_sec = time.time() - t0
        u = getattr(resp, "usage", None)
        if u is not None:
            usage.prompt_tokens = getattr(u, "prompt_tokens", 0) or 0
            usage.completion_tokens = getattr(u, "completion_tokens", 0) or 0
            detail = getattr(u, "completion_tokens_details", None)
            usage.reasoning_tokens = (getattr(detail, "reasoning_tokens", 0) or 0) if detail else 0
        choice = resp.choices[0]
        usage.finish_reason = getattr(choice, "finish_reason", "") or ""
        content = (getattr(choice.message, "content", "") or "").strip()

        if usage.finish_reason == "length":
            self._record(usage, "truncated_output", {"task": task})
            raise TruncatedOutput(usage.completion_tokens, int(self.profile["max_tokens"]), self.slot)
        if not content:
            self._record(usage, "empty_completion", {"task": task})
            raise EmptyCompletion(
                f"[{self.slot}] 返回为空（finish={usage.finish_reason}, "
                f"reasoning={usage.reasoning_tokens}）task={task}"
            )
        min_chars = int(self.profile.get("min_output_chars") or 0)
        if len(content) < min_chars:
            self._record(usage, "output_too_short", {"task": task, "chars": len(content)})
            raise OutputTooShort(len(content), min_chars, self.slot)

        self._record(usage, "OK", {"task": task})
        return content

    def complete_json(self, messages: Sequence[dict[str, str]], *, task: str = "",
                      **kwargs: Any) -> dict[str, Any]:
        """要求 JSON 输出并解析；解析失败即抛，不做「返回空 dict」的兜底。"""
        raw = self.complete(messages, task=task, **kwargs)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._record(self.last_usage or Usage(slot=self.slot), "json_parse_failed",
                         {"task": task, "raw_head": raw[:200]})
            raise LLMError(f"[{self.slot}] 返回非合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"[{self.slot}] 期望 JSON object，实际 {type(data).__name__}")
        return data

    # ---------------- 观测
    def summarize(self) -> dict[str, Any]:
        n = len(self.usage_history)
        if not n:
            return {"slot": self.slot, "calls": 0}
        return {
            "slot": self.slot,
            "calls": n,
            "avg_reasoning": round(sum(u.reasoning_tokens for u in self.usage_history) / n, 1),
            "avg_latency": round(sum(u.latency_sec for u in self.usage_history) / n, 2),
            "max_visible": max(u.visible_tokens for u in self.usage_history),
            "truncated": sum(1 for u in self.usage_history if u.finish_reason == "length"),
        }


__all__ = [
    "LLMError", "ConfigError", "TruncatedOutput", "EmptyCompletion",
    "OutputTooShort", "ClassificationError", "Usage", "LLMClient", "record_degraded",
]
