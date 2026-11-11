#!/usr/bin/env python3
"""发布凭证回查回归（离线，用桩替换 urlopen，不联网）

锁死：标题精确匹配、查不到返回空、无 cookie 直接空、接口异常不影响发布结论。
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import src.publish_evidence as ev  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f" — {extra}" if extra else ""))


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def _stub(payload: dict):
    def _fake_urlopen(req, timeout=0):
        return _Resp(json.dumps(payload).encode())
    return _fake_urlopen


ARCHIVES = {"code": 0, "data": {"arc_audits": [
    {"Archive": {"bvid": "BV1xx411c7mD", "title": "Anthropic冲刺纳斯达克", "pubdate": 1}},
    {"Archive": {"bvid": "BV1yy411c7mE", "title": "Gemini 4 Carbon登顶三榜", "pubdate": 2}},
]}}

CK = {"SESSDATA": "x", "bili_jct": "y"}


def main() -> int:
    print("=== 发布凭证回查回归 ===")
    real = ev.urllib.request.urlopen

    print("\n[1] 正常回查")
    ev.urllib.request.urlopen = _stub(ARCHIVES)
    try:
        check("标题精确命中 → bvid",
              ev.resolve_bvid_by_title("Anthropic冲刺纳斯达克", CK, tries=1) == "BV1xx411c7mD")
        check("标题不存在 → 空串",
              ev.resolve_bvid_by_title("根本没这条", CK, tries=1) == "")
        check("前后空格容错",
              ev.resolve_bvid_by_title("  Anthropic冲刺纳斯达克  ", CK, tries=1) == "BV1xx411c7mD")
        check("list_archives 能列稿件", len(ev.list_archives(CK)) == 2)
    finally:
        ev.urllib.request.urlopen = real

    print("\n[2] 边界")
    check("无 cookie → 空串（不请求）", ev.resolve_bvid_by_title("任意", {}) == "")
    check("空标题 → 空串", ev.resolve_bvid_by_title("", CK, tries=1) == "")

    print("\n[3] 接口异常不能抛")
    def _boom(req, timeout=0):
        raise TimeoutError("stub timeout")
    ev.urllib.request.urlopen = _boom
    try:
        check("异常 → 返回空串而不是抛错",
              ev.resolve_bvid_by_title("Anthropic冲刺纳斯达克", CK, tries=1) == "")
    finally:
        ev.urllib.request.urlopen = real

    print(f"\n✅ PASS {PASS}   ❌ FAIL {FAIL}")
    if FAIL:
        print("PUBLISH EVIDENCE CONTRACT FAIL")
        return 1
    print("PUBLISH EVIDENCE CONTRACT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
