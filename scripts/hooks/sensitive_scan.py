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
# hook 是**子进程**跑（引擎 spawn），拿不到父进程的 sys.path，
# 所以凡是 import src.* 的 hook 都必须自己把项目根塞进来（否则 ModuleNotFoundError: src）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
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


def _split_draft(draft) -> tuple[str, str]:
    """把 draft 拆成 (会原样发布的文本, 流水线会自动改写的文本)。

    缺陷①的根因就是这两类没分开：标题/正文/封面文案会原样进物料与发布，
    而旁白在 `align_tts` 那步会被 `src/safety/rewrites.py` 统一改写。
    前者命中敏感词必须 block，后者命中属于「门禁预览」，改写后不算违规。
    """
    if not isinstance(draft, dict):
        return C.text_of(draft), ""
    REWRITABLE_KEYS = {"hook_narration", "scenes", "segment_narration", "narration", "subtitle"}
    # fail-closed：凡是不在「会被 aligner 改写」白名单里的字符串字段，一律当作会原样发布
    # （曾经漏了 draft.body —— 敏感词写进 body 就完全扫不到，负向用例直接变绿）
    persisted = [str(v) for k, v in draft.items()
                 if k not in REWRITABLE_KEYS and isinstance(v, str) and v.strip()]
    rewritable = [str(draft.get("hook_narration") or "")]
    for sc in draft.get("scenes") or []:
        if isinstance(sc, dict):
            for k in ("segment_narration", "narration", "subtitle"):
                if sc.get(k):
                    rewritable.append(str(sc[k]))
    return "\n".join(p for p in persisted if p), "\n".join(p for p in rewritable if p)


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or C.read_json(C.artifact(p, "draft_script"))
    narration = c.get("narration") or ""

    from src.safety.rewrites import apply_rewrites

    draft_persisted, draft_rewritable = _split_draft(draft)
    # 原样发布的部分：命中即 block
    persisted = "\n".join(v for v in (
        str(man.get("title") or ""),
        str(man.get("body") or ""),
        draft_persisted,
        str(c.get("cover_text") or ""),
    ) if v)
    # 会被 aligner 改写的部分：先按改写表预览，改完仍命中才算违规
    rewritable_raw = "\n".join(v for v in (draft_rewritable, C.text_of(narration)) if v)
    rewritable, rewritten = apply_rewrites(rewritable_raw)

    if not (persisted.strip() or rewritable.strip()):
        C.fail("sensitive_scan: 无可扫描文本（manifest/draft 全空）")

    hits: list[str] = []
    for w in _words():
        low = w.lower()
        if low in persisted.lower():
            hits.append(f"sensitive_word:{w}@title/body（原样发布，请改物料）")
        elif low in rewritable.lower():
            hits.append(f"sensitive_word:{w}@旁白（改写表未覆盖）")
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, persisted + "\n" + rewritable_raw)
        if m:
            hits.append(f"injection_pattern:{m.group(0)[:24]}")

    if hits:
        C.fail("sensitive_scan: 命中 " + ", ".join(sorted(set(hits))[:6]), hits=sorted(set(hits))[:20])

    C.emit(True, patch={
        "sensitive_scan": "clean",
        "scanned_chars": len(persisted) + len(rewritable),
        "rewritten": rewritten,
    })


if __name__ == "__main__":
    main()
