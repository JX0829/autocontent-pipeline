#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布结论汇总回归（src/publish_report）。

要防的是 2026-11-15 那个真实事故：
    流水线发了 B站 → publish.json 记 1/1、审批卡改成「1/1 B站」；
    之后单平台 CLI 补发视频号 → 又发一张「1/1 视频号」的新卡。
    同一个 run 两张卡、各说一半，台账/明细/卡片三处对不上。

断言：
  1. 同平台新结果**覆盖**旧结果（跨 published/failed/skipped 三桶一起清）
  2. 门禁跳过项（有 gate）不会被误判成 failed
  3. 三次合并后回执是「✅ 已发布 3/3」
  4. publish.json / publish_detail.md 三平台都在
  5. 有 approval_card.json 时走「原地更新」，且标题是 3/3；没有卡时才发独立回执
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from src import publish_report  # noqa: E402
from src.feishu.card_builder import publish_receipt_text  # noqa: E402

PASS = FAIL = 0


def ok(name: str, extra: str = "") -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {name}{(' — ' + extra) if extra else ''}")


def bad(name: str, extra: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  ❌ {name}{(' — ' + extra) if extra else ''}")


def test_merge_semantics() -> None:
    old = {
        "published": [{"platform": "bilibili", "msg_id": "BV1"}],
        "failed": [{"platform": "channels", "error": "登录态已过期"}],
        "skipped": [],
    }
    # 1) 视频号补发成功 → 从 failed 移到 published，不留双份
    rep = publish_report.merge_results(old, [{"platform": "channels", "msg_id": "export/1"}])
    chans = [x for x in rep["published"] if x["platform"] == "channels"]
    if len(chans) == 1 and not rep["failed"]:
        ok("同平台新结果覆盖旧结果（failed → published 不重复）")
    else:
        bad("同平台新结果覆盖旧结果", f"published={rep['published']} failed={rep['failed']}")

    # 2) 门禁跳过项保持 skipped
    rep2 = publish_report.merge_results(old, [{"platform": "douyin", "gate": "ledger",
                                              "reason": "已发布过"}])
    skip_ok = (rep2["skipped"] and rep2["skipped"][0]["platform"] == "douyin"
               and not any(x["platform"] == "douyin" for x in rep2["failed"]))
    if skip_ok:
        ok("门禁跳过项归 skipped（不误判 failed）")
    else:
        bad("门禁跳过项归 skipped", f"skipped={rep2['skipped']} failed={rep2['failed']}")

    # 3) 三次分批合并 = 3/3 一条回执
    rep3: dict = {}
    for item in ({"platform": "bilibili", "msg_id": "BV1"},
                 {"platform": "douyin", "msg_id": "readback_1"},
                 {"platform": "channels", "msg_id": "export/1"}):
        rep3 = publish_report.merge_results(rep3, [item])
    receipt = publish_receipt_text(rep3, "测试标题")
    if "3/3" in receipt:
        ok("三次分批发布合并为 3/3", receipt)
    else:
        bad("三次分批发布合并为 3/3", receipt)


def test_write_and_card() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pubrep-"))
    run_root = tmp / "demo-1"
    run_root.mkdir()

    rep: dict = {}
    for item in ({"platform": "bilibili", "msg_id": "BV1"},
                 {"platform": "douyin", "msg_id": "readback_1"}):
        rep = publish_report.merge_results(rep, [item])
    publish_report.write_report(run_root, "demo-1", rep, "测试标题")

    pj = json.loads((run_root / "publish.json").read_text(encoding="utf-8"))
    detail = (run_root / "publish_detail.md").read_text(encoding="utf-8")
    if len(pj["published"]) == 2 and "BV1" in detail and "readback_1" in detail:
        ok("publish.json / publish_detail.md 双落盘")
    else:
        bad("publish.json / publish_detail.md 双落盘", detail[:120])

    # 有审批卡 → 原地更新（打桩拦截 update_card_status，不真发飞书）
    (run_root / "approval_card.json").write_text(json.dumps({"msg_id": "om_x"}), encoding="utf-8")
    from src.approval import feishu as approval_feishu

    captured: dict = {}

    def fake_update(run_id, header, template="blue", lines=None):  # noqa: ANN001
        captured.update({"run_id": run_id, "header": header, "lines": lines or []})
        return True

    real = getattr(approval_feishu, "update_card_status", None)
    approval_feishu.update_card_status = fake_update          # type: ignore[assignment]
    try:
        rep = publish_report.merge_and_finalize(
            run_root, [{"platform": "channels", "msg_id": "export/1"}], "测试标题")
    finally:
        if real is not None:
            approval_feishu.update_card_status = real          # type: ignore[assignment]

    if captured.get("run_id") == "demo-1" and "3/3" in (captured.get("header") or ""):
        ok("有审批卡 → 原地更新成 3/3", captured["header"])
    else:
        bad("有审批卡 → 原地更新成 3/3", str(captured))

    if len(rep["published"]) == 3:
        ok("merge_and_finalize 合并三平台", f"{len(rep['published'])} 条")
    else:
        bad("merge_and_finalize 合并三平台", str(rep["published"]))

    if any("channels" in str(x) for x in captured.get("lines") or []):
        ok("卡片明细里带每家平台一行")
    else:
        bad("卡片明细里带每家平台一行", str(captured.get("lines")))


def main() -> int:
    test_merge_semantics()
    test_write_and_card()
    print(f"\nPASS {PASS}  FAIL {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
