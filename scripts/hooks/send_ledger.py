#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hook.send_ledger（gate, block）—— 发送台账幂等闸门。

发布是最不可逆的一步，必须先查「这个实体今天是不是已经发过这个平台」。
台账：`data/send_ledger.jsonl`（append-only，外部确认后才写 —— 由发布步骤负责追加）。

命中同一 (entity_key, platform, date) → block，**绝不重复发**。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

LEDGER = C.ledger_path("send_ledger")


def main() -> None:
    p = C.load_payload()
    c = C.ctx(p)
    man = c.get("manifest") or {}
    draft = c.get("draft") or {}
    title = str(draft.get("title") or man.get("title") or "")
    if not title:
        C.fail("send_ledger: 缺标题，无法计算 entity_key")

    entity_key = str(c.get("entity_key") or C.normalize_text(title)[:120])
    platform = str(c.get("platform") or "")
    if not platform:
        C.fail("send_ledger: 缺 platform，无法判定是否已发（不接受“不知道发哪”的发布）")

    date = str(c.get("publish_date") or C.today())
    rows = C.read_jsonl(LEDGER, limit=5000)
    for r in rows:
        if (str(r.get("entity_key")) == entity_key
                and str(r.get("platform")) == platform
                and str(r.get("date")) == date):
            C.fail(f"send_ledger: {date} 已在 {platform} 发过同一实体（task={r.get('task_id')}）",
                   ledger_hit=r)

    C.emit(True, patch={"ledger_checked": len(rows), "entity_key": entity_key,
                        "platform": platform, "publish_date": date})


if __name__ == "__main__":
    main()
