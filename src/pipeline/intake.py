#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.8 投料通道（spec_enhancement.md §5 T1.8 / §4.9.2）

三种载体，**同一份 `manifest.schema.json` 校验**，产出结构完全一致的 manifest：

    cli        --material /path/to/manifest.json
    stdin      --material -              （管道/cat）
    feishu     --material feishu         （轮询 inbox/*.json，处理完移入 inbox/.done/）

铁律：
  · 载体只负责「取到 JSON」，**不做任何字段补全**（`forbid_per_carrier_field_fill`）
  · 解析与校验只有一条路：`manifest.load_manifest()` → `manifest.validate()`
  · 校验不过抛 `ManifestInvalid`（带逐条原因），绝不静默跳过
  · feishu 载体按 `msg_id` 去重：同一消息二次出现直接跳过（防重放）

自检：
    python3 -m src.pipeline.intake --selftest
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_INBOX = PROJ / "inbox"
CARRIERS = ("cli", "stdin", "feishu")


class ManifestInvalid(ValueError):
    """物料不符合契约。`errors` 为逐条原因。"""

    def __init__(self, errors: list[str], carrier: str = ""):
        self.errors = errors
        self.carrier = carrier
        where = f"[{carrier}] " if carrier else ""
        super().__init__(f"{where}物料不符合 manifest.schema.json：\n  - " + "\n  - ".join(errors))


@dataclass
class Acquired:
    carrier: str
    raw: dict[str, Any]
    msg_id: str | None = None
    source: str = ""

    def canonical(self) -> str:
        """规范化字节串：用于断言三载体产出等价（键排序 + 紧凑分隔符）。"""
        return json.dumps(self.raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------ 载体取字节

def _read_path(path: str | Path) -> tuple[Any, str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"物料文件不存在: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"物料文件为空: {p}")
    return json.loads(text), str(p)


def _read_stdin() -> tuple[Any, str]:
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError("stdin 未收到任何内容")
    return json.loads(text), "stdin"


def _read_inbox(inbox: Path) -> tuple[Any, str, str | None]:
    """取 inbox 中第一条**未处理**的物料；处理完由 `_retire` 移入 .done/。"""
    if not inbox.exists():
        raise FileNotFoundError(f"飞书投料箱不存在: {inbox}")
    done = inbox / ".done"
    done.mkdir(exist_ok=True)
    for f in sorted(inbox.glob("*.json")):
        stem = f.stem
        if (done / f.name).exists():
            continue  # 已处理过：防重放
        data = json.loads(f.read_text(encoding="utf-8"))
        msg_id = str(data.get("msg_id") or stem)
        if (done / f"{msg_id}.json").exists():
            continue
        return data, str(f), msg_id
    raise FileNotFoundError(f"投料箱为空或全部已处理: {inbox}")


def _retire(source: str, msg_id: str | None) -> None:
    """把已消费的 inbox 文件移入 .done/（保留原名，按 msg_id 幂等）。"""
    p = Path(source)
    if not p.exists() or p.parent.name == ".done":
        return
    done = p.parent / ".done"
    done.mkdir(exist_ok=True)
    target = done / (f"{msg_id}.json" if msg_id else p.name)
    if target.exists():
        target.unlink()
    shutil.move(str(p), str(target))


# ------------------------------------------------------------------ 统一入口

def detect_carrier(material: str) -> str:
    if material == "-":
        return "stdin"
    if material.lower() in ("feishu", "feishu_file"):
        return "feishu"
    return "cli"


def acquire(material: str, *, inbox: Path | str | None = None) -> Acquired:
    """唯一入口：按载体取到 JSON → 统一解析 → 统一校验。"""
    from src.pipeline.manifest import load_manifest, validate

    carrier = detect_carrier(material)
    msg_id: str | None = None
    if carrier == "stdin":
        payload, source = _read_stdin()
    elif carrier == "feishu":
        payload, source, msg_id = _read_inbox(Path(inbox) if inbox else DEFAULT_INBOX)
    else:
        payload, source = _read_path(material)

    manifest = load_manifest(payload)
    errors = validate(manifest)
    if errors:
        raise ManifestInvalid(errors, carrier)

    # msg_id 是载体元数据，不属于物料本身：剥离后再产出，保证三载体结构一致
    raw = dict(manifest.raw)
    carrier_msg_id = raw.pop("msg_id", None)
    msg_id = str(carrier_msg_id) if carrier_msg_id else msg_id

    if carrier == "feishu":
        _retire(source, msg_id)
    return Acquired(carrier=carrier, raw=raw, msg_id=msg_id, source=source)


# ------------------------------------------------------------------ 自检

SAMPLE = {
    "title": "投料自检样例标题",
    "source_url": "https://example.com/x",
}


def selftest() -> int:
    """三条通路各跑一次，断言产出字节级一致 + 非法物料必被拒。"""
    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="intake_selftest_"))
    try:
        cli_file = tmp / "m.json"
        cli_file.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
        a_cli = acquire(str(cli_file))

        stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(SAMPLE, ensure_ascii=False))
        try:
            a_stdin = acquire("-")
        finally:
            sys.stdin = stdin_backup

        inbox = tmp / "inbox"
        inbox.mkdir()
        (inbox / "msg_001.json").write_text(
            json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
        a_feishu = acquire("feishu", inbox=inbox)

        # 二次投料必须被去重（防重放）
        (inbox / "msg_001.json").write_text(
            json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
        replay_blocked = False
        try:
            acquire("feishu", inbox=inbox)
        except FileNotFoundError:
            replay_blocked = True

        # 非法物料必须被拒（缺 body/source_url 的意图由 anyOf 判定）
        bad = tmp / "bad.json"
        bad.write_text(json.dumps({"title": "太短"}, ensure_ascii=False), encoding="utf-8")
        rejected = False
        try:
            acquire(str(bad))
        except ManifestInvalid as exc:
            rejected = bool(exc.errors)

        same = a_cli.canonical() == a_stdin.canonical() == a_feishu.canonical()
        print(f"  cli     : {a_cli.source} → {len(a_cli.canonical())} bytes")
        print(f"  stdin   : {a_stdin.source} → {len(a_stdin.canonical())} bytes")
        print(f"  feishu  : {a_feishu.source} → {len(a_feishu.canonical())} bytes (msg_id={a_feishu.msg_id})")
        print(f"  IDENTICAL {same}")
        print(f"  REPLAY_BLOCKED {replay_blocked}")
        print(f"  INVALID_REJECTED {rejected}")
        ok = same and replay_blocked and rejected
        print(f"SELFTEST {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T1.8 投料通道")
    ap.add_argument("--selftest", action="store_true", help="三通路自检")
    ap.add_argument("--material", default=None, help="cli 路径 / - / feishu")
    ap.add_argument("--inbox", default=None, help="feishu 投料箱目录")
    ap.add_argument("--json", action="store_true", help="打印规范化 JSON")
    args = ap.parse_args(argv)

    if args.selftest:
        print("=== 投料通道自检 ===")
        return selftest()
    if not args.material:
        ap.print_help()
        return 2
    try:
        got = acquire(args.material, inbox=args.inbox)
    except (ManifestInvalid, FileNotFoundError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2
    print(f"carrier={got.carrier} source={got.source} msg_id={got.msg_id}")
    if args.json:
        print(got.canonical())
    return 0


if __name__ == "__main__":
    sys.exit(main())
