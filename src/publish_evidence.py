"""发布凭证回查（不依赖 bilibili_api / playwright，纯 stdlib）

为什么要有它：台账的 `post_id` 一直空着 —— 上传接口的返回值里常常没有 bvid，
出问题时报错只能写"未知"，人工还得自己去后台翻。

这里用只读的稿件列表接口按标题回查 bvid，作为**发布成功的旁证**：
  · 稿件刚落库有几秒延迟 → 默认重试 3 次、间隔 5s
  · 查不到就返回 ""，绝不影响发布结论（凭证缺失 ≠ 发布失败）
"""
from __future__ import annotations

import json
import time
import urllib.request

BILI_ARCHIVES = "https://member.bilibili.com/x/web/archives?status=is_pubed&pn=1&ps=20"


def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in (cookies or {}).items() if isinstance(v, str))


def resolve_bvid_by_title(title: str, cookies: dict, tries: int = 3,
                          sleep_sec: float = 5.0, timeout: float = 20.0) -> str:
    """按标题在「已发布稿件」列表里回查 bvid（只读，不发布任何东西）。"""
    title = str(title or "").strip()
    if not title:
        return ""
    ck = _cookie_header(cookies)
    if not ck:
        return ""

    for i in range(max(1, tries)):
        try:
            req = urllib.request.Request(BILI_ARCHIVES, headers={
                "Cookie": ck,
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://member.bilibili.com/",
            })
            data = json.load(urllib.request.urlopen(req, timeout=timeout))
            for item in ((data.get("data") or {}).get("arc_audits") or []):
                arc = item.get("Archive") or {}
                if str(arc.get("title") or "").strip() == title:
                    return str(arc.get("bvid") or "")
        except Exception as exc:  # noqa: BLE001 — 凭证回查失败不能影响发布结论
            if i >= tries - 1:
                print(json.dumps({"bvid_lookup_warn": f"{type(exc).__name__}: {exc}"},
                                 ensure_ascii=False))
        if i < tries - 1:
            time.sleep(sleep_sec)
    return ""


def list_archives(cookies: dict, timeout: float = 20.0) -> list[dict]:
    """列出已发布稿件（只读，给人核对用）。"""
    ck = _cookie_header(cookies)
    if not ck:
        return []
    req = urllib.request.Request(BILI_ARCHIVES, headers={
        "Cookie": ck, "User-Agent": "Mozilla/5.0",
        "Referer": "https://member.bilibili.com/"})
    data = json.load(urllib.request.urlopen(req, timeout=timeout))
    out = []
    for item in ((data.get("data") or {}).get("arc_audits") or []):
        arc = item.get("Archive") or {}
        out.append({"bvid": arc.get("bvid"), "title": arc.get("title"),
                    "pubdate": arc.get("pubdate")})
    return out
