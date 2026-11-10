#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.sensitive_scan（gate, block）—— 出厂文本敏感词与注入扫描。

两个词库：
  1. `config/sensitive_words.txt`（人工维护的业务敏感词，75 条）
  2. 内置注入/越狱/凭据模式（与 comment_funnel 一级防护同源思路）

扫描范围：manifest.title/body、draft 全文、narration、封面文案。
命中即 block，reason 只回报命中位置与词（**不回显完整敏感上下文**）。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

INJECTION_PATTERNS = [
    r"(?i)api[ _-]?key", r"(?i)sk-[a-zA-Z0-9]{16,}", r"(?i)system\s*prompt",
    r"(?i)prompt\s*injection", r"(?i)jailbreak", r"(?i)ignore\s+(all\s+)?(previous|above)\s+instructions?",
    r"(?i)forget\s+(all\s+)?(previous|above)\s+instructions?", r"(?i)dan\s*mode",
    r"系统提示", r"忽略(之前|以上|所有)指令", r"越狱", r"数据库密码", r"后台地址", r"\.env",
]


def _words() -> list[str]:
    f = C.PROJ / "config" / "sensitive_words.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w and not w.startswith("#"):
            out.append(w)
    return out


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script"))
    narration = c.get("narration") or ""

    targets = {
        "title": str(man.get("title") or ""),
        "body": str(man.get("body") or ""),
        "draft": C.text_of(draft),
        "narration": C.text_of(narration),
        "cover_text": str(c.get("cover_text") or ""),
    }
    blob = "\n".join(v for v in targets.values() if v)
    if not blob.strip():
        C.fail("sensitive_scan: 无可扫描文本（manifest/draft 全空）")

    hits: list[str] = []
    lowered = blob.lower()
    for w in _words():
        if w.lower() in lowered:
            hits.append(f"sensitive_word:{w}")
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, blob)
        if m:
            hits.append(f"injection_pattern:{m.group(0)[:24]}")

    if hits:
        C.fail("sensitive_scan: 命中 " + ", ".join(sorted(set(hits))[:6]), hits=sorted(set(hits))[:20])

    C.emit(True, patch={"sensitive_scan": "clean", "scanned_chars": len(blob)})


if __name__ == "__main__":
    main()
