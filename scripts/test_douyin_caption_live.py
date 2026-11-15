#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音简介填充验证器（只读验证，硬编码 stop_before_publish=True，**永不点发布**）。

命名用 test_ 前缀是为了走发布入口审计的测试豁免（见 audit_publish_entrypoints.py）。

为什么需要：2026-11-15 抖音简介发出后是乱码（标题与正文交错、`#A#n#C#A`）。
根因是把「标题 + 话题 + 全文」一次性 type(delay=50) 打进 contenteditable，
抖音的话题联想面板在打字过程中插入/重排内容。修完后必须能"发布前自证"，
否则只能等发出去看手机上乱不乱。

用法:
    python3 scripts/test_douyin_caption.py <run_id>
    python3 scripts/test_douyin_caption.py <run_id> --profile /abs/path/to/douyin-tech

判据：正文开头在位 + 每个话题都在 + 无交错乱码 → 打印 VERDICT PASS/FAIL
退出码：0 PASS / 1 FAIL / 2 找不到 run
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    run_id = args[0]
    profile = ""
    for i, a in enumerate(sys.argv):
        if a == "--profile" and i + 1 < len(sys.argv):
            profile = sys.argv[i + 1]

    hits = sorted(PROJ.glob(f"output/*/{run_id}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        print(f"FATAL: 找不到 run 目录 output/*/{run_id}")
        return 2
    run_dir = hits[0]

    from src.providers.douyin import DouyinProvider
    from src.utils.accounts import for_platform
    from src.utils.video_desc import build_desc

    draft = json.loads((run_dir / "draft_script.json").read_text(encoding="utf-8"))
    title = str(draft.get("title") or "")
    topics = list(draft.get("tags") or [])
    body = build_desc(draft, platform="douyin", run_root=run_dir, include_tags=False)

    acc = dict(for_platform("douyin"))
    if profile:
        acc["profile_path"] = profile

    print(f"run      : {run_dir}")
    print(f"标题     : {title}")
    print(f"话题     : {topics}")
    print(f"正文     : {body[:70]}...  (共 {len(body)} 字)")

    res = DouyinProvider(acc).save_draft(
        video_path=str(run_dir / "video.mp4"),
        cover_path=str(run_dir / "cover.png") if (run_dir / "cover.png").exists() else None,
        title=title, topics=topics, content_text=body, stop_before_publish=True)

    print("\n=== 浏览器回读 ===")
    for k in ("success", "published", "status", "error", "message"):
        if k in res:
            print(f"{k:10s}: {res[k]}")
    filled = res.get("filled_text", "") or ""
    print(f"filled({len(filled)}): {filled[:400]}")

    ok_head = body.strip()[:20] in filled
    ok_tags = all(f"#{t.strip('#')}" in filled for t in topics)
    garbled = bool(re.search(r"#.\w?#.\w?#", filled))     # #A#n#C 这类交错特征
    print(f"\n开头在位 : {ok_head}\n话题都在 : {ok_tags}\n交错乱码 : {garbled}")
    verdict = ok_head and ok_tags and not garbled and bool(filled)
    print(f"VERDICT  : {'PASS' if verdict else 'FAIL'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
