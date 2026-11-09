#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T2.1 Hook 执行引擎（spec_enhancement.md §5 T2.1）

职责：按 `pipeline.yaml` 的 `hooks` 注册表，把 hook 当成**外部进程**调用，
统一协议、统一超时、统一留痕。引擎本身不含任何业务判断。

协议：
    stdin  ← {"task_id","run_root","step_id","artifacts":{...},"context":{...}}
    stdout → {"ok": bool, "patch"?: object, "reason"?: string}

语义：
    gate      ok=false → 按 `on_fail`：block 中止 / warn 记录续行
    transform ok=true  → `patch` 合并进 context
    observe   忽略返回值，仅落日志
    超时/崩溃 视为 ok=false，**且必须写 degraded_events(kind=skipped)**（不许静默）

CLI：
    python3 -m src.pipeline.hooks --hook hook.envelope_validate --pipeline pipeline.yaml < payload.json
    python3 -m src.pipeline.hooks --list
    python3 -m src.pipeline.hooks --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.pipeline.contracts import load_pipeline

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_TIMEOUT = 30.0


class HookError(RuntimeError):
    """hook 调用层错误（注册表缺失 / 协议违规）。"""


@dataclass
class HookResult:
    name: str
    kind: str
    ok: bool
    patch: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    elapsed: float = 0.0
    exit_code: int | None = None
    stdout_tail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "hook": self.name, "kind": self.kind, "ok": self.ok,
            "fault": self.fault, "reason": self.reason,
            "elapsed_sec": round(self.elapsed, 2),
            "exit_code": self.exit_code, "patch_keys": sorted(self.patch),
        }

    @property
    def fault(self) -> bool:
        """基础设施故障（impl 缺失/超时/崩溃/协议违规），与"业务判定为 false"区分。"""
        return self.reason.startswith(("impl_missing", "timeout", "hook_crashed", "协议违规"))


# ------------------------------------------------------------------ 注册表

def load_hooks(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hooks = cfg.get("hooks") or {}
    if not hooks:
        raise HookError("pipeline.yaml 未注册任何 hook")
    return hooks


def hook_spec(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    hooks = load_hooks(cfg)
    if name not in hooks:
        raise HookError(f"未注册的 hook: {name}（可选: {', '.join(sorted(hooks))}）")
    return hooks[name] or {}


def impl_path(spec: dict[str, Any]) -> Path:
    impl = spec.get("impl")
    if not impl:
        raise HookError("hook 缺少 impl 声明")
    p = Path(impl)
    return p if p.is_absolute() else PROJ / p


# ------------------------------------------------------------------ 适配器
# 少数 hook 的 impl 是**既有模块/CLI**（不是 scripts/hooks/* 的 stdin/stdout 协议）。
# 不改动它们的源码（那些文件由 T3.2/T4.x 接管重做），在引擎侧做协议适配。
# 待对应任务把 impl 改成标准 hook 协议后，删掉这里的条目即可。
ADAPTERS: dict[str, tuple[str, str]] = {
    "hook.landscape_guard": ("scripts.landscape_guard", "check_landscape"),
    "hook.card_render": ("src.feishu.card_builder", "build_approval_card"),
}


def _run_adapter(name: str, payload: dict[str, Any]) -> HookResult | None:
    """返回 None 表示该 hook 不走适配器。"""
    entry = ADAPTERS.get(name)
    if not entry:
        return None
    module_name, func_name = entry
    t0 = time.time()
    sys.path.insert(0, str(PROJ))
    try:
        import importlib
        module = importlib.import_module(module_name)
    except Exception as exc:
        return HookResult(name=name, kind="gate", ok=False,
                          reason=f"impl_missing: 适配器无法导入 {module_name}（{exc}）",
                          elapsed=time.time() - t0)

    try:
        if name == "hook.landscape_guard":
            func = getattr(module, func_name)
            c = payload.get("context") or {}
            video = (payload.get("artifacts") or {}).get(c.get("video_key") or "video")
            video = (payload.get("artifacts") or {}).get("video_16_9") or video
            if not video:
                return HookResult(name=name, kind="gate", ok=False,
                                  reason="adapter_args_missing: 缺 artifacts.video")
            path = Path(str(video))
            path = path if path.is_absolute() else Path(str(payload.get("run_root") or PROJ)) / path
            ok, report = func(str(path), str(c.get("expect_text") or ""))
            return HookResult(name=name, kind="gate", ok=bool(ok),
                              patch=report if isinstance(report, dict) else {},
                              reason="" if ok else str((report or {}).get("reason") or "landscape_guard 拦截"),
                              elapsed=time.time() - t0)

        # hook.card_render
        builder = getattr(module, "FeishuCardBuilder")
        card_args = (payload.get("context") or {}).get("card_args")
        if not isinstance(card_args, dict):
            return HookResult(name=name, kind="transform", ok=False,
                              reason="adapter_args_missing: 缺 context.card_args",
                              elapsed=time.time() - t0)
        card = builder.build_approval_card(**card_args)
        return HookResult(name=name, kind="transform", ok=True,
                          patch={"card": card}, elapsed=time.time() - t0)
    except Exception as exc:
        return HookResult(name=name, kind="gate", ok=False,
                          reason=f"hook_crashed(adapter): {type(exc).__name__}: {exc}",
                          elapsed=time.time() - t0)


# ------------------------------------------------------------------ 调用

def _degraded(kind: str, detail: dict[str, Any]) -> None:
    """降级留痕（复用 T1.5 的 record_degraded；导入失败也必须落盘，不许静默）。"""
    try:
        from src.llm.client import record_degraded
        record_degraded(kind, detail)
        return
    except Exception:
        pass
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": kind, **detail}
        path = PROJ / "data" / "degraded_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run_hook(name: str, payload: dict[str, Any], cfg: dict[str, Any] | None = None,
             timeout: float = DEFAULT_TIMEOUT) -> HookResult:
    """执行一个 hook。永不抛业务异常：所有失败都折叠成 ok=False + reason。"""
    cfg = cfg or load_pipeline()
    spec = hook_spec(cfg, name)
    kind = str(spec.get("type") or "observe")

    adapted = _run_adapter(name, payload)
    if adapted is not None:
        adapted.kind = kind
        return adapted

    path = impl_path(spec)

    if not path.exists():
        return HookResult(name=name, kind=kind, ok=False, reason=f"impl_missing: {path}")

    stdin_data = json.dumps(payload, ensure_ascii=False)
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            input=stdin_data, capture_output=True, text=True, timeout=timeout,
            cwd=str(PROJ),
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        _degraded("skipped", {"hook": name, "reason": "timeout", "timeout_sec": timeout})
        return HookResult(name=name, kind=kind, ok=False,
                          reason=f"timeout: 超过 {timeout:g}s", elapsed=elapsed)
    elapsed = time.time() - t0

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        _degraded("skipped", {"hook": name, "reason": f"exit_{proc.returncode}", "stderr": " | ".join(tail)})
        return HookResult(name=name, kind=kind, ok=False,
                          reason=f"hook_crashed(exit={proc.returncode}): {' | '.join(tail)[:300]}",
                          elapsed=elapsed, exit_code=proc.returncode,
                          stdout_tail=(proc.stdout or "")[-300:])

    out = (proc.stdout or "").strip()
    if not out:
        _degraded("skipped", {"hook": name, "reason": "empty_stdout"})
        return HookResult(name=name, kind=kind, ok=False, reason="协议违规: stdout 为空",
                          elapsed=elapsed, exit_code=proc.returncode)

    try:
        data = json.loads(out.splitlines()[-1])
    except json.JSONDecodeError as exc:
        _degraded("skipped", {"hook": name, "reason": "bad_json", "head": out[:200]})
        return HookResult(name=name, kind=kind, ok=False, reason=f"协议违规: stdout 非 JSON（{exc}）",
                          elapsed=elapsed, exit_code=proc.returncode, stdout_tail=out[-300:])

    if not isinstance(data, dict) or "ok" not in data:
        return HookResult(name=name, kind=kind, ok=False,
                          reason="协议违规: 缺少 ok 字段", elapsed=elapsed,
                          exit_code=proc.returncode, stdout_tail=out[-300:])

    return HookResult(
        name=name, kind=kind, ok=bool(data.get("ok")),
        patch=data.get("patch") or {} if isinstance(data.get("patch"), dict) else {},
        reason=str(data.get("reason") or ""), elapsed=elapsed,
        exit_code=proc.returncode, stdout_tail=out[-300:],
    )


def run_chain(names: Iterable[str], payload: dict[str, Any], cfg: dict[str, Any] | None = None,
              timeout: float = DEFAULT_TIMEOUT) -> tuple[dict[str, Any], list[HookResult]]:
    """顺序执行一组 hook，按语义合并结果。返回 (新 context, 结果列表)。"""
    cfg = cfg or load_pipeline()
    context = dict(payload.get("context") or {})
    results: list[HookResult] = []

    for name in names:
        res = run_hook(name, {**payload, "context": context}, cfg, timeout)
        results.append(res)
        if res.kind == "transform" and res.ok:
            context.update(res.patch)
        elif res.kind == "gate" and not res.ok:
            on_fail = str((hook_spec(cfg, name).get("on_fail")) or "block")
            if on_fail == "block":
                break  # 中止后续（调用方据 results[-1] 判定失败）
        elif res.kind == "observe":
            continue
    return context, results


# ------------------------------------------------------------------ 自检

SELFTEST_MANIFEST = {
    "title": "开源项目 5B 参数跑通多模态推理",
    "body": ("该开源项目用 5B 参数在 8GB 显存消费级显卡上跑通多模态推理，"
             "官方称吞吐 120 tokens/s，代码已托管在 GitHub 并附带复现脚本。"),
    "source_url": "https://example.com/project",
}
SELFTEST_DRAFT = {
    "title": "5B 参数 8GB 显存跑多模态",
    "body": ("这个开源项目只用了 5B 参数，在 8GB 显存的消费级显卡上就跑通了多模态推理。"
             "官方给出的吞吐是 120 tokens/s，等于把门槛直接按到家用机的水平。"
             "更关键的是代码和复现脚本都已经公开，谁都能拉下来自己跑一遍。"),
}

_SELFTEST_DB_SQL = """
create table if not exists content_tasks (
  task_id text primary key, source_url text not null, entity_key text not null unique,
  theme text not null, target_account_id text not null, status text not null,
  draft_script_json text, aligned_render_json text, video_path text, cover_path text,
  xhs_cards_dir text, platform_draft_id text, published_video_id text,
  fact_summary_150 text, resource_keyword text,
  created_at datetime default current_timestamp, updated_at datetime default current_timestamp,
  has_seed_comment integer default 0, seed_comment_text text, seed_comment_at datetime,
  video_landscape_path text, cover_landscape_path text
);
"""


def _material_block() -> str:
    """自检用：直接复用 material_bundle hook 的输出，避免重复实现打包逻辑。"""
    from src.pipeline.manifest import load_manifest
    return load_manifest(SELFTEST_MANIFEST).to_prompt_block()


def _reset_state(payload: dict[str, Any], data_dir: Path) -> None:
    """每跑一个 hook 前把可变状态复位，避免前一 hook 的写入影响后一 hook 的判定。"""
    db = Path(str((payload.get("context") or {}).get("db_path")))
    if db.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(str(db))
            conn.execute("delete from content_tasks")
            conn.commit(); conn.close()
        except sqlite3.Error:
            pass
    for f in data_dir.glob("*.jsonl"):
        f.unlink(missing_ok=True)


_COVER_TEXT = "L A N D S C A P E   C O V E R   H E A D L I N E"


def _render_cover(path: Path, w: int = 1920, h: int = 1080) -> bool:
    """用 PIL 渲染一张带横排大字的白底封面（landscape_guard 需要可 OCR 的文字）。

    PIL 不可用时返回 False，调用方退回 testsrc（此时 guard 会按设计拦截纯图形帧）。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    fonts = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for fp in fonts:
        try:
            font = ImageFont.truetype(fp, 80)
            break
        except OSError:
            continue
    if font is None:
        return False
    bbox = draw.textbbox((0, 0), _COVER_TEXT, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, h // 2 - 40), _COVER_TEXT, fill="black", font=font)
    draw.rectangle([(w - tw) // 2, h // 2 + 80, (w + tw) // 2, h // 2 + 88], fill="black")
    img.save(path)
    return True


def _make_fixtures(root: Path) -> dict[str, Any]:
    """构造一套"应该全部通过"的真实物料：真视频、真封面、真台账、临时 DB。"""
    (root / "manifest.json").write_text(json.dumps(SELFTEST_MANIFEST, ensure_ascii=False), encoding="utf-8")
    (root / "draft_script.json").write_text(json.dumps(SELFTEST_DRAFT, ensure_ascii=False), encoding="utf-8")

    cover = root / "cover.png"
    has_text_cover = _render_cover(cover)
    if not has_text_cover:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "testsrc=size=1920x1080:rate=1", "-frames:v", "1", str(cover)],
            capture_output=True, text=True, timeout=60,
        )

    video = root / "video.mp4"
    if has_text_cover:  # 有文字封面时把封面做成 3s 视频，guard 的 OCR 判据才有意义
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(cover),
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-shortest", "-t", "3", str(video)],
            capture_output=True, text=True, timeout=90,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=25:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
            capture_output=True, text=True, timeout=90,
        )

    db = root / "state.db"
    import sqlite3
    conn = sqlite3.connect(str(db)); conn.executescript(_SELFTEST_DB_SQL); conn.commit(); conn.close()

    narration = ["这个开源项目只用了 5B 参数。"]
    return {
        "task_id": "selftest_t1",
        "run_root": str(root),
        "step_id": "selftest",
        "artifacts": {
            "manifest": "manifest.json",
            "draft_script": "draft_script.json",
            "video": "video.mp4",
            "cover": "cover.png",
            "cover_source": "cover.png",
        },
        "context": {
            "manifest": SELFTEST_MANIFEST,
            "draft": SELFTEST_DRAFT,
            "narration": narration,
            "material_block": _material_block(),
            "account": "douyin-tech-main",
            "platform": "douyin",
            "aspect": "16:9",
            "db_path": str(db),
            "cover_text": SELFTEST_MANIFEST["title"],
            "expect_text": "",           # 空串：guard 跳过 OCR 命中判定，只校验尺寸/覆盖度
            "card_args": {
                "task_id": "selftest_t1",
                "title": SELFTEST_MANIFEST["title"],
                "topics": ["开源", "多模态推理"],
                "content_text": SELFTEST_DRAFT["body"][:120],
                "resource_keyword": "example-project",
                "fact_summary": SELFTEST_MANIFEST["body"][:150],
                "duration_sec": 3.0,
                "video_size_mb": round(video.stat().st_size / 1048576, 2),
            },
        },
    }


def selftest(verbose: bool = False) -> int:
    """逐个 hook 跑真实调用，期望全部 ok=true；再对 3 个关键闸门做负向验证。"""
    cfg = load_pipeline()
    root = Path(tempfile.mkdtemp(prefix="hooks_selftest_"))
    data_dir = root / "data"
    data_dir.mkdir()
    os.environ["AUTOCONTENT_DATA_DIR"] = str(data_dir)

    payload = _make_fixtures(root)
    hooks = load_hooks(cfg)

    print(f"=== Hook 引擎自检（{len(hooks)} 个 hook）===")
    print(f"  夹具: {root}")
    fails = 0
    for name in hooks:
        _reset_state(payload, data_dir)
        res = run_hook(name, payload, cfg)
        mark = "✅" if res.ok else "❌"
        print(f"  {mark} {name:<32} {res.kind:<9} {res.elapsed:5.2f}s  {res.reason[:70]}")
        if not res.ok:
            fails += 1

    # 负向验证：闸门必须真的会拦
    negatives = [
        ("hook.sensitive_scan", {"context": {**payload["context"],
                                             "draft": {"body": "这里有 api_key 泄露风险"}}},
         "敏感词/注入应被拦"),
        ("hook.material_check", {"context": {**payload["context"],
                                             "manifest": {"title": "只有标题而已"}}},
         "物料缺事实应被拦"),
        ("hook.title_length_guard", {"context": {**payload["context"],
                                                 "draft": {"title": "短"}}},
         "标题过短应 warn"),
    ]
    print("\n  负向验证（期望 ok=false）:")
    for name, override, desc in negatives:
        res = run_hook(name, {**payload, **override}, cfg)
        mark = "✅" if not res.ok else "❌"
        if res.ok:
            fails += 1
        print(f"  {mark} {name:<32} ok={res.ok}  {desc} → {res.reason[:60]}")

    os.environ.pop("AUTOCONTENT_DATA_DIR", None)
    print(f"\nSELFTEST {'PASS' if fails == 0 else 'FAIL'}（失败 {fails} 项）")
    return 0 if fails == 0 else 1


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Hook 执行引擎（T2.1）")
    ap.add_argument("--hook", default=None, help="要执行的 hook 名")
    ap.add_argument("--chain", default=None, help="逗号分隔的 hook 链")
    ap.add_argument("--pipeline", default=str(PROJ / "pipeline.yaml"))
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--list", action="store_true", help="列出注册的 hook")
    ap.add_argument("--selftest", action="store_true", help="全量自检")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--strict", action="store_true",
                    help="ok=false 也返回非零（默认：只要 hook 正常执行完就返回 0）")
    args = ap.parse_args(argv)

    cfg = load_pipeline(args.pipeline)

    if args.list:
        for name, spec in load_hooks(cfg).items():
            p = impl_path(spec)
            print(f"{name:<32} {str(spec.get('type')):<9} {'✓' if p.exists() else '✗'} {spec.get('impl')}")
        return 0

    if args.selftest:
        return selftest(verbose=args.json)

    if args.chain:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        names = [n.strip() for n in args.chain.split(",") if n.strip()]
        ctx, results = run_chain(names, payload, cfg, args.timeout)
        blocked = [r for r in results if r.kind == "gate" and not r.ok
                   and str(hook_spec(cfg, r.name).get("on_fail") or "block") == "block"]
        out = {"ok": not blocked, "context": ctx,
               "results": [r.as_dict() for r in results]}
        if blocked:
            out["reason"] = f"gate_blocked: {blocked[0].name} → {blocked[0].reason}"
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if not blocked else 1

    if not args.hook:
        ap.print_help()
        return 2

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "reason": f"payload_not_json: {exc}"}, ensure_ascii=False))
        return 2

    res = run_hook(args.hook, payload, cfg, args.timeout)
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2) if args.json
          else json.dumps({"ok": res.ok, "reason": res.reason, "patch": res.patch}, ensure_ascii=False))
    # 退出码语义：
    #   0 = hook 正常执行完（ok 可能为 false —— 那是业务判定，不是引擎故障）
    #   1 = 引擎层故障（impl 缺失 / 超时 / 崩溃 / 协议违规）或 --strict 下的 ok=false
    if res.fault:
        return 1
    if args.strict and not res.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
