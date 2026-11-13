#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「黄仁勋辞任」6 次出片的缺陷①②③④回归（2026-11-12）

① 敏感词「推上」误杀：门禁与 aligner 用同一张改写表，能改写的词不算违规；
③ 失败 run 的 intake 占位污染 dedupe：只把「真出过片」的行算进排他黑名单；
④ resume 死锁：断点回标 DONE 但产物缺失 → 回退重做；对账只对账已完成部分。

②（同内容强制复用旧 run_root，换 run-id 绕不过）由 fix/p0-publish-blockers 的
`resolve_run_identity` 覆盖，本文件用纯函数断言顺带守住。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.pipeline.runner import reconcile, resolve_run_identity, rewind_to_missing  # noqa: E402
from src.safety.rewrites import apply_rewrites  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


CFG = {"steps": [
    {"id": "intake", "produces": ["manifest.json"]},
    {"id": "llm", "produces": ["draft_script.json"]},
    {"id": "align_tts", "produces": ["props/aligned_render.json"]},
    {"id": "render_vertical", "produces": ["video.mp4", "cover.png"]},
    {"id": "render_landscape", "produces": ["video_16_9.mp4", "cover_16_9.png"]},
]}


def _mk(root: Path, rels: list[str]) -> None:
    for r in rels:
        p = root / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_rewind() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="nvda_rewind_"))
    # 断点说前 4 步都完成（start_index=4），但 render_vertical 的产物不在
    _mk(tmp, ["manifest.json", "draft_script.json", "props/aligned_render.json"])
    i, why = rewind_to_missing(CFG, tmp, 4)
    check("④ 断点回退到缺产物的 render_vertical", i == 3, f"start=4 → {i} | {why}")

    _mk(tmp, ["video.mp4", "cover.png"])
    i2, why2 = rewind_to_missing(CFG, tmp, 4)
    check("④ 产物齐全时不回退", i2 == 4, f"start=4 → {i2} | {why2}")

    # 空文件也算缺失（渲染中途被杀）
    (tmp / "video.mp4").write_bytes(b"")
    i3, _ = rewind_to_missing(CFG, tmp, 4)
    check("④ 0 字节产物也算缺失", i3 == 3, f"start=4 → {i3}")


def test_reconcile_upto() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="nvda_recon_"))
    _mk(tmp, ["manifest.json", "draft_script.json", "props/aligned_render.json"])
    missing_upto3 = reconcile(CFG, tmp, upto=3)
    check("④ upto=3 时不再把未跑的 render 判成缺失", missing_upto3 == [], str(missing_upto3))
    missing_all = reconcile(CFG, tmp, upto=None)
    check("④ upto=None 才会报 render 缺失（旧行为）", len(missing_all) == 4, str(len(missing_all)))


def test_dedupe_filter() -> None:
    """把 hotspot_extractor 的 SQL 复制到临时库里跑，验证占位行不污染黑名单。"""
    tmp = Path(tempfile.mkdtemp(prefix="nvda_dedupe_"))
    db = tmp / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table content_tasks (task_id text, source_url text, entity_key text,
            theme text, target_account_id text, status text, draft_script_json text,
            fact_summary_150 text, created_at text default '2026-11-12 15:00:00');
        create table pipeline_runs (run_id text, task_id text, status text);
    """)
    conn.execute("insert into content_tasks (task_id,entity_key,theme,status) values "
                 "('nvda-1','黄仁勋辞任','黄仁勋辞任 英伟达换CEO','intake')")
    conn.execute("insert into content_tasks (task_id,entity_key,theme,status) values "
                 "('dsv41-1','一张3090跑763b','一张3090跑763B模型','DISPATCHED')")
    conn.commit()
    conn.close()

    sql = ("SELECT ct.task_id, ct.entity_key, ct.theme, ct.draft_script_json, ct.created_at "
           "FROM content_tasks ct WHERE (? IS NULL OR ct.task_id != ?) "
           "  AND NOT (lower(coalesce(ct.status,'')) IN ('intake','blocked','failed','aborted','abandoned') "
           "           AND NOT EXISTS (SELECT 1 FROM pipeline_runs r WHERE r.task_id = ct.task_id AND r.status = 'DONE')) "
           "ORDER BY ct.created_at DESC LIMIT ?")

    def topics(exclude=None):
        c = sqlite3.connect(db)
        rows = c.execute(sql, (exclude, exclude, 50)).fetchall()
        c.close()
        return [r[0] for r in rows]

    got = topics(exclude="nvda-6")
    check("③ 失败 run 的 intake 占位不进黑名单", "nvda-1" not in got, str(got))
    check("③ 已发布的行仍在黑名单", "dsv41-1" in got, str(got))

    conn = sqlite3.connect(db)
    conn.execute("insert into pipeline_runs values ('nvda-1','nvda-1','DONE')")
    conn.commit()
    conn.close()
    got2 = topics(exclude="nvda-9")
    check("③ 有 DONE run 的同一主题仍被排他", "nvda-1" in got2, str(got2))


def test_rewrites() -> None:
    text = "他把公司推上四万亿美元，推特上都在转 twitter 的原文"
    out, applied = apply_rewrites(text)
    check("① 推上/推特/twitter 全部改写", not any(w in out for w in ("推上", "推特", "twitter")), out)
    check("① 改写项如实回报", len(applied) == 3, str(applied))


def test_run_identity() -> None:
    """② 显式 --run-id 必须优先，不被同内容历史 run 覆盖。"""
    content_run = {"run_id": "nvda-resign-1", "status": "BLOCKED",
                   "run_root": "/x/output/20261112/nvda-resign-1", "task_id": "nvda-resign-1"}
    new_root = Path("/x/output/20261113/nvda-resign-6")
    rid, root, _, mode, force_new = resolve_run_identity(
        content_run, None, explicit_run_id="nvda-resign-6", resume=False,
        new_run_id="nvda-resign-6", new_run_root=new_root, task_id="nvda-resign-6")
    check("② 显式 run-id 不被旧 run 劫持",
          rid == "nvda-resign-6" and root == new_root and mode == "explicit_new",
          f"{rid} | {mode} | force_new={force_new}")
    check("② 显式新建要给同内容旧 run 让路（force_new）", force_new is True)

    rid2, root2, _, mode2, force2 = resolve_run_identity(
        content_run, None, explicit_run_id="", resume=False,
        new_run_id="auto-1", new_run_root=new_root, task_id="auto-1")
    check("② 未显式指定时仍复用历史 run（保留续跑能力）",
          rid2 == "nvda-resign-1" and mode2 == "retry" and force2 is False,
          f"{rid2} | {mode2}")

    done_run = dict(content_run, status="DONE")
    rid3, _, _, mode3, _ = resolve_run_identity(
        done_run, None, explicit_run_id="nvda-resign-9", resume=False,
        new_run_id="nvda-resign-9", new_run_root=new_root, task_id="nvda-resign-9")
    check("② 同内容已 DONE 仍幂等跳过（GATE-3 契约不被破坏）", mode3 == "skip", mode3)


def main() -> int:
    test_rewind()
    test_reconcile_upto()
    test_dedupe_filter()
    test_rewrites()
    test_run_identity()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
