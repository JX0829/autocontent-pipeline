#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静默失败静态扫描器（T2.5 · 防复发主力）

扫描四类"静默失败"反模式，支持基线：**只报告新增违例**，让存量债务不阻塞 CI，
但新增一处就红。

  1. 吞异常      except ...: pass | continue | return None/""
  2. 静默跳过    if not X: continue
  3. 兜底默认值  return "D"
  4. 截断当正常  finish_reason == "length" 且在 raise 之前就 return

用法：
    python3 scripts/audit_silent_failure.py --init-baseline   # 首次：生成基线
    python3 scripts/audit_silent_failure.py                   # 之后：只报新增
    python3 scripts/audit_silent_failure.py --json

退出码：无新增 0；有新增 1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = PROJ / "config" / "silent_baseline.json"   # 必须入库，否则 CI 无基线
SCAN_DIRS = (PROJ / "src", PROJ / "scripts")
SKIP_PARTS = {"_deprecated", "node_modules", "__pycache__", ".backup"}

PATTERNS = [
    # 1) 吞异常：except ...: pass / continue / return None/""/[]
    ("swallow_exception", re.compile(
        r'except[^\n]*:[ \t]*(?:\n[ \t]*)?(?:pass|continue|return[ \t]+(?:None|""|\[\]))')),
    # 2) 静默跳过：if not X: continue
    ("silent_skip", re.compile(r'^[ \t]*if[ \t]+not[ \t]+\w+[ \t]*:[ \t]*(?:continue|return)[ \t]*$',
                               re.MULTILINE)),
    # 3) 兜底默认值：return "D"
    ("fallback_letter", re.compile(r'return[ \t]+["\']D["\']')),
    # 4) 截断当正常：finish_reason == "length" 且附近没有 raise
    ("truncation_ignored", re.compile(r'finish_reason[^=\n]*==[ \t]*["\']length["\']')),
]


def scan_file(path: Path) -> list[dict]:
    hits: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return hits
    lines = text.splitlines()
    for name, rx in PATTERNS:
        for m in rx.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            snippet = (lines[line_no - 1] if line_no - 1 < len(lines) else "").strip()[:120]
            # 文档/注释里的示例不算违例（否则 `"""finish_reason == 'length'"""` 会被误报）
            if snippet.startswith(("#", '"""', "'''", "*")) or '"""' in snippet:
                continue
            # 截断当正常：附近（±6 行）有 raise 视为已处理
            if name == "truncation_ignored":
                window = "\n".join(lines[max(0, line_no - 6): line_no + 6])
                if re.search(r"raise\s+\w*Error|raise\s+Truncat|raise\s+\w+\(", window):
                    continue
            hits.append({"file": str(path.relative_to(PROJ)), "line": line_no,
                         "pattern": name, "snippet": snippet})
    return hits


def _iter_files():
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for py in sorted(d.rglob("*.py")):
            if SKIP_PARTS & set(py.parts):
                continue
            if py.name == "audit_silent_failure.py":
                continue
            yield py


def scan() -> list[dict]:
    hits: list[dict] = []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for py in sorted(d.rglob("*.py")):
            if SKIP_PARTS & set(py.parts):
                continue
            if py.name == "audit_silent_failure.py":
                continue  # 扫描器自身的正则不算违例
            hits.extend(scan_file(py))
    return hits


def assign_keys(hits: list[dict]) -> None:
    """给每条违例算稳定键：`file::pattern::snippet指纹#occurrence`。

    不含行号 —— 在违例上方插代码不会把老问题误报成"新增"；
    但同一文件里第 4 处一模一样的 `except Exception: pass` 会因 occurrence 递增而被发现。
    """
    import hashlib
    hits.sort(key=lambda h: (h["file"], h["line"]))
    seen: dict[tuple[str, str, str], int] = {}
    for h in hits:
        snippet = re.sub(r"\s+", "", h.get("snippet") or "")[:80]
        fp = hashlib.sha1(snippet.encode("utf-8")).hexdigest()[:10]
        k = (h["file"], h["pattern"], fp)
        seen[k] = seen.get(k, 0) + 1
        h["key"] = f"{h['file']}::{h['pattern']}::{fp}#{seen[k]}"


def key_of(h: dict) -> str:
    return h["key"]


def check_seen_before_send() -> list[dict]:
    """T5.1 专用检查：状态写 REPLIED / seen 时，同一 SQL 块内必须有外部回执凭据。

    反模式（QA#10 的根因）：
        cur.execute("INSERT ... status) VALUES (..., 'REPLIED')")   # 一条都没发就标已回复
    合规形态：
        UPDATE comment_interactions SET status='REPLIED', external_msg_id=? WHERE ...
    """
    hits: list[dict] = []
    for path in _iter_files():
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if "REPLIED" not in line:
                continue
            if line.lstrip().startswith(("#", "//", "*")):
                continue
            lo, hi = max(0, i - 8), min(len(lines), i + 8)
            block = "\n".join(lines[lo:hi])
            writes = ("INSERT" in block.upper()) or ("UPDATE" in block.upper())
            if not writes:
                continue
            if "external_msg_id" in block:
                continue
            hits.append({"file": str(path.relative_to(PROJ)), "line": i + 1,
                         "pattern": "seen_before_send",
                         "snippet": line.strip()[:100]})
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="静默失败反模式扫描")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    ap.add_argument("--init-baseline", action="store_true", help="生成/覆盖基线")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", choices=["seen-before-send"],
                    help="专项检查（T5.1）：REPLIED 是否在无外部回执时就写入")
    args = ap.parse_args(argv)

    if args.check == "seen-before-send":
        hits = check_seen_before_send()
        if args.json:
            print(json.dumps(hits, ensure_ascii=False, indent=2))
        else:
            if hits:
                print(f"=== seen-before-send 违规 {len(hits)} 处 ===")
                for h in hits:
                    print(f"  {h['file']}:{h['line']}  {h['snippet']}")
            else:
                print("seen-before-send 检查通过：REPLIED 均带外部回执")
        return 1 if hits else 0

    baseline_path = Path(args.baseline)
    hits = scan()
    assign_keys(hits)
    keys = [key_of(h) for h in hits]

    if args.init_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps({
            "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "count": len(hits),
            "violations": keys,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"=== 生成基线 {baseline_path} ===")
        print(f"  存量违例: {len(hits)}")
        by_pattern: dict[str, int] = {}
        for h in hits:
            by_pattern[h["pattern"]] = by_pattern.get(h["pattern"], 0) + 1
        for name, n in sorted(by_pattern.items(), key=lambda x: -x[1]):
            print(f"    {name:<22} {n}")
        for h in hits[:15]:
            print(f"    · {h['file']}:{h['line']}  [{h['pattern']}] {h['snippet'][:70]}")
        if len(hits) > 15:
            print(f"    … 其余 {len(hits) - 15} 条见基线文件")
        return 0

    if not baseline_path.exists():
        print(f"基线不存在（{baseline_path}）。先跑 --init-baseline 生成存量白名单。")
        return 1

    baseline = set(json.loads(baseline_path.read_text(encoding="utf-8")).get("violations") or [])
    new_hits = [h for h in hits if key_of(h) not in baseline]

    if args.json:
        print(json.dumps({"total": len(hits), "baseline": len(baseline),
                          "new": new_hits}, ensure_ascii=False, indent=2))
    else:
        print("=== 静默失败扫描 ===")
        print(f"  当前违例: {len(hits)}    基线: {len(baseline)}    新增违例: {len(new_hits)}")
        for h in new_hits:
            print(f"    ✗ {h['file']}:{h['line']}  [{h['pattern']}] {h['snippet'][:80]}")
        if not new_hits:
            print("  结论：无新增静默失败反模式")
    return 0 if not new_hits else 1


if __name__ == "__main__":
    sys.exit(main())
