#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.6 模型切换自检（spec_enhancement.md §5 T1.6）

用途：**任何模型变更前必须跑通**（附录 B SOP 强制步骤）。
每一种能力槽位都发一次真实最小请求，逐项断言，任一失败即 `EXIT=1`。

对 `kind: llm` 的槽位断言 6 项（QA#1 #2 的现场判据）：
    1. 调用成功（无 unknown provider / 鉴权失败）
    2. content 非空
    3. finish_reason != "length"（reasoning 没吃满预算）
    4. json_mode 槽位的返回可被 json.loads 解析
    5. 延迟 <= expected_latency_sec × 2
    6. 输出长度 >= min_output_chars

对 `kind: tts` 的槽位：真实合成一小段音频，断言有音频字节产出且延迟 <= timeout_sec。

用法：
    python3 scripts/model_switch_check.py                # 全槽位真实自检
    python3 scripts/model_switch_check.py --slot llm.fast
    python3 scripts/model_switch_check.py --dry-run      # 只列槽位与 profile，不发请求
    python3 scripts/model_switch_check.py --json         # 机器可读，便于 CI

退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

try:  # 让 --dry-run 也能显示真实模型名
    from dotenv import load_dotenv
    load_dotenv(PROJ / ".env")
except ImportError:  # pragma: no cover
    pass

from src.llm.client import (  # noqa: E402
    ConfigError,
    EmptyCompletion,
    LLMClient,
    LLMError,
    OutputTooShort,
    TruncatedOutput,
)
from src.pipeline.contracts import load_pipeline  # noqa: E402

# 自检用提示：短、稳定、对 JSON 槽位要求可解析的最小结构
PROMPT_TEXT = "用一句话回答：1+1 等于几？（只回答数字与一句不超过 20 字的说明）"
PROMPT_JSON = (
    '以 JSON 返回，键为 {"title": "...", "body": "..."}；'
    "body 为 100-140 字中文短评，主题：开源项目用 5B 参数在消费级显卡跑多模态推理。"
)
TTS_TEXT = "自检，一二三。"  # 6 字，够短不烧额度


@dataclass
class SlotResult:
    slot: str
    kind: str
    model: str
    budget: str
    latency: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)
    detail: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values()) and not self.error

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot, "kind": self.kind, "model": self.model,
            "budget": self.budget, "latency_sec": round(self.latency, 2),
            "checks": self.checks, "passed": self.passed,
            "detail": self.detail, "error": self.error,
        }


# ------------------------------------------------------------------ llm 槽位

def check_llm(slot: str, prov: dict[str, Any], cfg: dict[str, Any]) -> SlotResult:
    profile = prov.get("profile") or {}
    json_mode = bool(profile.get("json_mode"))
    res = SlotResult(
        slot=slot, kind="llm",
        model=os.getenv(str((prov.get("env") or {}).get("model", "")), "?"),
        budget=f"max_tokens={profile.get('max_tokens')} reserve={profile.get('reasoning_reserve')}",
    )
    prompt = PROMPT_JSON if json_mode else PROMPT_TEXT

    try:
        client = LLMClient(slot, cfg)
    except ConfigError as exc:
        res.error = f"配置错误: {exc}"
        return res

    t0 = time.time()
    try:
        content = client.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            task=f"switch_check:{slot}",
        )
        res.latency = time.time() - t0
        res.checks["1_调用成功"] = True
        res.checks["2_content非空"] = bool(content.strip())
        # 3/4/5/6 在拿到 usage 后判定
        usage = client.last_usage
        res.checks["3_未截断"] = (usage.finish_reason != "length") if usage else False
        if json_mode:
            try:
                json.loads(content)
                res.checks["4_JSON可解析"] = True
            except json.JSONDecodeError:
                res.checks["4_JSON可解析"] = False
        else:
            res.checks["4_JSON可解析"] = True  # 非 json_mode 槽位不适用
        exp = float(profile.get("expected_latency_sec") or 0)
        res.checks["5_延迟达标"] = res.latency <= exp * 2 if exp else True
        min_chars = int(profile.get("min_output_chars") or 0)
        res.checks["6_长度达标"] = len(content) >= min_chars
        res.detail = (
            f"{len(content)}字 | reasoning={usage.reasoning_tokens if usage else '?'} "
            f"visible={usage.visible_tokens if usage else '?'} | 上限 {exp * 2:.0f}s"
        )
    except TruncatedOutput as exc:
        res.latency = time.time() - t0
        res.checks.update({"1_调用成功": True, "3_未截断": False})
        res.error = str(exc)
    except EmptyCompletion as exc:
        res.latency = time.time() - t0
        res.checks.update({"1_调用成功": True, "2_content非空": False})
        res.error = str(exc)
    except OutputTooShort as exc:
        res.latency = time.time() - t0
        res.checks.update({"1_调用成功": True, "2_content非空": True, "6_长度达标": False})
        res.error = str(exc)
    except LLMError as exc:
        res.latency = time.time() - t0
        res.checks["1_调用成功"] = False
        res.error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # 网络/鉴权等
        res.latency = time.time() - t0
        res.checks["1_调用成功"] = False
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ------------------------------------------------------------------ tts 槽位

def check_tts(slot: str, prov: dict[str, Any]) -> SlotResult:
    profile = prov.get("profile") or {}
    env = prov.get("env") or {}
    voice = os.getenv(str(env.get("voice", "")), "")
    rate = os.getenv(str(env.get("rate", "")), "") or "+0%"
    timeout = float(profile.get("timeout_sec") or 0)
    res = SlotResult(slot=slot, kind="tts", model=f"{voice} @ {rate}",
                     budget=f"timeout_sec={timeout:g}")

    if not voice:
        res.error = f"环境变量缺失: {env.get('voice')}"
        return res

    async def _run() -> int:
        import edge_tts  # 延迟导入，缺包时不影响 llm 槽位自检
        comm = edge_tts.Communicate(TTS_TEXT, voice, rate=rate)
        total = 0
        async for chunk in comm.stream():
            if chunk.get("type") == "audio":
                total += len(chunk.get("data") or b"")
        return total

    t0 = time.time()
    try:
        size = asyncio.run(_run())
        res.latency = time.time() - t0
        res.checks["1_调用成功"] = True
        res.checks["2_音频非空"] = size > 0
        res.checks["5_延迟达标"] = res.latency <= (timeout or 1e9)
        res.detail = f"音频 {size} 字节 | 上限 {timeout:g}s"
    except Exception as exc:
        res.latency = time.time() - t0
        res.checks["1_调用成功"] = False
        res.error = f"{type(exc).__name__}: {exc}"
    return res


# ------------------------------------------------------------------ 主流程

def model_slots(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(name, prov) for name, prov in (cfg.get("providers") or {}).items()
            if (prov or {}).get("kind") in ("llm", "tts")]


def print_dry_run(cfg: dict[str, Any], slots: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"=== 模型槽位清单（{len(slots)} 个，--dry-run 不发请求）===")
    for name, prov in slots:
        kind = prov.get("kind")
        prof = prov.get("profile") or {}
        env = prov.get("env") or {}
        model = os.getenv(str(env.get("model") or env.get("voice") or ""), "(未设置)")
        if kind == "llm":
            summary = (f"max_tokens={prof.get('max_tokens')} reasoning_reserve={prof.get('reasoning_reserve')} "
                       f"timeout={prof.get('timeout_sec')}s json_mode={prof.get('json_mode')} "
                       f"min_output_chars={prof.get('min_output_chars')} "
                       f"expected_latency={prof.get('expected_latency_sec')}s")
        else:
            summary = f"timeout={prof.get('timeout_sec')}s rate={os.getenv(str(env.get('rate','')), '?')}"
        print(f"  {name:<10} kind={kind:<4} model={model:<34} {summary}")
    print("\nDRY RUN OK（未发起任何真实请求）")


def print_table(results: list[SlotResult]) -> None:
    head = ("槽位", "kind", "model", "预算", "延迟", "结果", "断言")
    rows = []
    for r in results:
        fails = [k for k, v in r.checks.items() if not v]
        rows.append((
            r.slot, r.kind, r.model[:34], r.budget,
            f"{r.latency:.1f}s",
            "PASS" if r.passed else "FAIL",
            "全部通过" if not fails else "失败: " + ",".join(fails),
        ))
    widths = [max(len(str(x[i])) for x in [head] + rows) for i in range(len(head))]
    for i, row in enumerate([head] + rows):
        print("  " + "  ".join(str(v).ljust(widths[j]) for j, v in enumerate(row)).rstrip())
        if i == 0:
            print("  " + "  ".join("-" * w for w in widths))
    for r in results:
        if r.detail:
            print(f"    · {r.slot}: {r.detail}")
        if r.error:
            print(f"    ✗ {r.slot}: {r.error}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="模型切换自检（T1.6）")
    ap.add_argument("--slot", action="append", default=None,
                    help="只校验指定槽位，可重复；默认校验全部 llm/tts 槽位")
    ap.add_argument("--dry-run", action="store_true", help="只列槽位与 profile，不发请求")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--pipeline", default=str(PROJ / "pipeline.yaml"))
    args = ap.parse_args(argv)

    cfg = load_pipeline(args.pipeline)
    slots = model_slots(cfg)
    if args.slot:
        wanted = set(args.slot)
        unknown = wanted - {n for n, _ in slots}
        if unknown:
            print(f"[error] 未知槽位: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        slots = [(n, p) for n, p in slots if n in wanted]

    if args.dry_run:
        if args.json:
            print(json.dumps({n: p.get("profile") for n, p in slots}, ensure_ascii=False, indent=2))
        else:
            print_dry_run(cfg, slots)
        return 0

    print(f"=== 模型切换自检 {datetime.now():%Y-%m-%d %H:%M:%S} ===",
          file=sys.stderr if args.json else sys.stdout)
    results: list[SlotResult] = []
    for name, prov in slots:
        kind = prov.get("kind")
        results.append(check_llm(name, prov, cfg) if kind == "llm" else check_tts(name, prov))

    if args.json:
        print(json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print()
        print_table(results)

    passed = sum(1 for r in results if r.passed)
    if args.json:
        print(f"\nRESULT: {passed}/{len(results)} PASS", file=sys.stderr)
    else:
        print(f"\nRESULT: {passed}/{len(results)} PASS")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
