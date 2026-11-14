#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布入口收口审计（量产链路 B）

目的：发布只能走 RUNBOOK 认可的几条入口，防止散装脚本重新长出来。

允许的发布入口（白名单）：
  实现层
    src/providers/douyin.py / bilibili.py / channels_cdp.py
  编排层
    src/pipeline/executors.py                 （runner 的 dispatch 步）
  命令层（RUNBOOK §2③ / §5.1 / §5.2 / §7.1）
    scripts/publish_douyin_cdp.py
    scripts/publish_channels.py
    scripts/bilibili_live_uploader.py
    scripts/send_run_artifacts.py             （只发飞书素材，不对外发布）

其余任何文件出现 publish/save_draft 调用 → 判违规，退出 1。

用法：
  python3 scripts/audit_publish_entrypoints.py
  python3 scripts/audit_publish_entrypoints.py --verbose
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

WHITELIST = {
    "src/providers/douyin.py",
    "src/providers/bilibili.py",
    "src/providers/channels_cdp.py",
    "src/providers/base.py",
    "src/pipeline/executors.py",
    "scripts/publish_douyin_cdp.py",
    "scripts/publish_channels.py",
    "scripts/bilibili_live_uploader.py",
    "scripts/send_run_artifacts.py",
}

# 真正「对外发布」的动作签名（定义与调用都算）
PUBLISH_CALL = re.compile(
    r"("
    r"def\s+save_draft\s*\("
    r"|\.(?:save_draft|publish|publish_private|publish_video)\s*\("
    r"|video_uploader\.VideoUploader"
    r")"
)

# 扫描范围
SCAN_DIRS = ["src", "scripts"]
SKIP_DIRS = {"__pycache__", "_deprecated", "backup", "node_modules", ".venv", "external"}

# 测试脚本可以直调 provider：它们用不存在的临时路径做负向用例（如登录态/探活），
# 不会真的上传或发布。豁免必须显式打印出来，便于人工确认没有"真发"的测试。
# 注意：豁免只认 scripts/test_*.py 这一种命名，别把业务脚本写成 test_ 前缀。
TEST_EXEMPT = re.compile(r"^scripts/test_[A-Za-z0-9_]+\.py$")


def iter_py(root: Path):
    for d in SCAN_DIRS:
        base = root / d
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            yield p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="发布入口收口审计")
    ap.add_argument("--verbose", action="store_true", help="打印白名单与全部命中")
    args = ap.parse_args(argv)

    violations: list[tuple[str, int, str]] = []
    allowed_hits = 0
    exempt_files: set[str] = set()

    for p in iter_py(PROJ):
        rel = p.relative_to(PROJ).as_posix()
        exempt = bool(TEST_EXEMPT.match(rel))
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[WARN: 跳过不可读文件 {rel}: {exc}]", file=sys.stderr)
            continue
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if not PUBLISH_CALL.search(line):
                continue
            if rel in WHITELIST:
                allowed_hits += 1
                if args.verbose:
                    print(f"  ok   {rel}:{i}  {s[:70]}")
            elif exempt:
                exempt_files.add(rel)
                if args.verbose:
                    print(f"  test {rel}:{i}  {s[:70]}")
            else:
                violations.append((rel, i, s[:90]))

    print(f"发布入口审计：白名单内命中 {allowed_hits} 处，违规 {len(violations)} 处")
    if exempt_files:
        print(f"（豁免测试负向用例：{', '.join(sorted(exempt_files))}）")
    if violations:
        print("\n违规（这些文件不应直接调用发布接口，请改用 RUNBOOK 入口）：")
        for rel, i, s in violations:
            print(f"  ✗ {rel}:{i}  {s}")
        print("\n合法入口见 RUNBOOK §2③ / §5.1 / §5.2 / §7.1")
        return 1

    print("PUBLISH_ENTRYPOINTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
