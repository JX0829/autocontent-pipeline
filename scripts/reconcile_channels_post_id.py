#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号「作品 id 事后对账」：post_list 才是权威，台账/publish.json 要对齐。

背景（2026-11-21 真机实证）：发布后 90s 内 post_list 就命中了，但那一刻返回的
`objectId` 还是**临时 export id**；等作品真正发出去/过审后，同一条作品在
post_list 里的 `objectId` 会变成最终 id。于是出现：

    台账 post_id = export/UzFfBgAAxIWnVEg8QiSbk8zT4DCah2DCENS9YSUuPhxV-amqlw
    post_list   = export/UzFfBgAAxOWnBHgwXiSbk8zT4DCal9TJBH_l4btyitw0Qsj27Q   ← 真值

发布结论不变（作品确实在），但台账 id 是错的 → 后续 edit/删除/去重都会打偏。
这个脚本只做**对账**：按标题在 post_list 里找同一条作品，把真实 objectId 写回
publish.json 与台账（默认只读，加 --apply 才写，写前备份台账）。

用法：
  python3 scripts/reconcile_channels_post_id.py --run-dir output/20261121/xxx
  python3 scripts/reconcile_channels_post_id.py --run-dir ... --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from playwright.async_api import async_playwright  # noqa: E402

from src.providers.channels_edit import fetch_post_list, sanitize_title  # noqa: E402

CDP = "http://127.0.0.1:9334"


def _norm(s: str) -> str:
    return "".join(ch for ch in str(s or "") if ch.isalnum())


async def _find(title: str, size: int = 20) -> list[dict]:
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0] if b.contexts else await b.new_context()
        page = await ctx.new_page()
        try:
            await page.goto("https://channels.weixin.qq.com/platform",
                            wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            data = await fetch_post_list(page, size, 0)
        finally:
            await page.close()
    items = ((data.get("data") or {}).get("list") or [])
    # 平台上落地的是归一化后的标题（2.3B → 2点3B），两种都要能匹配上
    keys = {k for k in (_norm(title)[:8], _norm(sanitize_title(title)[0])[:8]) if len(k) >= 4}
    out = []
    for it in items:
        desc = (it.get("desc") or {})
        blob = _norm(desc.get("description")) + _norm(desc.get("title"))
        if any(k in blob for k in keys):
            out.append({"objectId": str(it.get("objectId") or ""),
                        "createTime": it.get("createTime"),
                        "first_line": str(desc.get("description") or "").split("\n")[0][:40]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--apply", action="store_true", help="把真实 id 写回 publish.json + 台账")
    ap.add_argument("--db", default=str(PROJ / "data" / "pipeline_state.db"))
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    pub_file = run_dir / "publish.json"
    pub = json.loads(pub_file.read_text(encoding="utf-8"))
    rows = [p for p in pub.get("published", []) if p.get("platform") == "channels"]
    if not rows:
        print("publish.json 里没有 channels 记录")
        return 1
    row = rows[0]
    recorded = str(row.get("msg_id") or "")
    title = str(row.get("title_used") or pub.get("title") or "")
    if not title:
        draft = run_dir / "draft_script.json"
        if draft.exists():
            d = json.loads(draft.read_text(encoding="utf-8"))
            title = str((d.get("draft") or d).get("title") or "")
    print(f"run={run_dir.name} 台账 id={recorded or '(空)'}\n标题={title}")

    hits = asyncio.run(_find(title))
    if not hits:
        print("❌ post_list 前 20 条没找到同标题作品（需要人工去后台看）")
        return 2
    print(f"post_list 命中 {len(hits)} 条：")
    for h in hits:
        print(f"  {h['objectId']}  {time.strftime('%F %H:%M', time.localtime(h['createTime'] or 0))}  {h['first_line']}")
    true_id = hits[0]["objectId"]

    # 台账真值才是要修的对象：可能被截断（真机事故：台账里存了 25 字符的前缀，
    # 而 publish.json 是完整的，只比 publish.json 会「看着一致、实际不一致」）。
    con = sqlite3.connect(args.db)
    try:
        ledger_rows = list(con.execute(
            "select publish_key, post_id from publish_ledger "
            "where platform='channels' and status='published' order by updated_at desc limit 20"))
    finally:
        con.close()
    bad = [(k, pid) for k, pid in ledger_rows
           if pid != true_id and (not pid or true_id.startswith(str(pid)) or str(pid).startswith(true_id[:20]))]
    if bad:
        print(f"⚠️ 台账里有 {len(bad)} 行 id 与 post_list 真值不一致（疑似截断）：")
        for k, pid in bad:
            print(f"  {k}  {pid}  →  {true_id}")
    if true_id == recorded and not bad:
        print("✅ 台账 id 与 post_list 一致，无需对账")
        return 0

    print(f"\n⚠️ id 不一致：发布记录 {recorded or '(空)'} → 真值 {true_id}")
    if not args.apply:
        print("（只读模式，未写入；确认后加 --apply）")
        return 0

    bak = Path(args.db).with_suffix(Path(args.db).suffix + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(args.db, bak)
    con = sqlite3.connect(args.db)
    try:
        cur = con.execute(
            "update publish_ledger set post_id=?, updated_at=datetime('now','localtime') "
            "where platform='channels' and post_id=?", (true_id, recorded))
        con.commit()
        print(f"台账按发布记录更新 {cur.rowcount} 行（备份 {bak.name}）")
        for k, pid in bad:
            c2 = con.execute(
                "update publish_ledger set post_id=?, updated_at=datetime('now','localtime') "
                "where publish_key=? and platform='channels'", (true_id, k))
            con.commit()
            print(f"台账按截断行修正 {c2.rowcount} 行：{k}")
        if cur.rowcount == 0:
            cur = con.execute(
                "update publish_ledger set post_id=?, updated_at=datetime('now','localtime') "
                "where platform='channels' and post_id is null and status='published'", (true_id,))
            con.commit()
            print(f"按「published 且无 id」兜底更新 {cur.rowcount} 行")
    finally:
        con.close()

    for r in rows:
        r["msg_id"] = true_id
        r["id_reconciled_from"] = recorded
    pub_file.write_text(json.dumps(pub, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"publish.json 已写回真值 id：{true_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
