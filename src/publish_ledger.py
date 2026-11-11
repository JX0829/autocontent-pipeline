"""发布台账（publish_ledger）：三个发布 CLI 的发布前查重 + 发布后记账。

为什么单独一张表：`send_ledger` 记的是「飞书投递」，语义完全不同。
平台发布一旦重复，是**不可撤回的公开内容**（抖音/视频号/B站各多一条），
所以查重必须在真正调平台接口之前，而且必须原子（并发两个 CLI 也不能都过）。

用法（CLI 侧）：

    from src.publish_ledger import PublishBlocked, check_and_claim, mark_published
    try:
        key, info = check_and_claim("douyin", account_id, video_path, title)
    except PublishBlocked as exc:
        print(f"⛔ 发布前查重拦截: {exc}")
        return 3
    ...真实发布...
    mark_published(key, post_id)

状态机：claimed → published | failed；published → deleted（人工删除后可重发）。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

PROJ = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJ / "data" / "pipeline_state.db"

_DDL = """
CREATE TABLE IF NOT EXISTS publish_ledger (
    publish_key  TEXT PRIMARY KEY,          -- sha256(platform|account|video_sha256)[:32]
    platform     TEXT NOT NULL,
    account      TEXT NOT NULL,
    video_sha256 TEXT NOT NULL,
    title        TEXT,
    post_id      TEXT,
    status       TEXT NOT NULL DEFAULT 'claimed',   -- claimed|published|failed|deleted
    reason       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
"""
_IDX = ("CREATE INDEX IF NOT EXISTS idx_publish_slot "
        "ON publish_ledger(platform, account, video_sha256)")


class PublishBlocked(RuntimeError):
    """查重拦截：这条内容在这个平台这个账号已经发过了（或正在发）。"""


def db_path() -> Path:
    return Path(os.getenv("SQLITE_DB_PATH") or DEFAULT_DB)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.execute(_DDL)
    conn.execute(_IDX)
    conn.commit()
    return conn


def video_sha256(path: str | Path, chunk: int = 1 << 20) -> str:
    """视频内容指纹（按内容算，跟文件名/路径无关）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def publish_key(platform: str, account: str, video_sha: str) -> str:
    return hashlib.sha256(f"{platform}|{account}|{video_sha}".encode()).hexdigest()[:32]


def check_and_claim(platform: str, account: str, video_path: str | Path,
                    title: str = "", *, path: Path | None = None) -> tuple[str, dict[str, Any]]:
    """发布前查重 + 原子占位。重复则抛 PublishBlocked。"""
    vp = Path(video_path)
    if not vp.exists():
        raise FileNotFoundError(f"视频不存在: {vp}")
    sha = video_sha256(vp)
    key = publish_key(platform, account, sha)
    now = _now()
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT status, post_id, updated_at FROM publish_ledger WHERE publish_key=?",
            (key,)).fetchone()
        if row and row[0] in ("published", "claimed"):
            if row[0] == "published":
                raise PublishBlocked(
                    f"{platform}/{account} 已发布过同一视频（post_id={row[1] or '未知'}，时间 {row[2]}）")
            raise PublishBlocked(
                f"{platform}/{account} 有未收尾的发布（claimed @ {row[2]}）——"
                f"先确认上次是否发出，再清理台账：python3 -m src.publish_ledger --release {key}")

        # 原子占位：只有插入成功（或把 failed/deleted 改回 claimed）才算抢到
        if row:
            cur = conn.execute(
                "UPDATE publish_ledger SET status='claimed', title=?, post_id=NULL, "
                "reason=NULL, updated_at=? WHERE publish_key=? AND status IN ('failed','deleted')",
                (title, now, key))
        else:
            cur = conn.execute(
                "INSERT INTO publish_ledger (publish_key, platform, account, video_sha256, title,"
                " status, created_at, updated_at) VALUES (?,?,?,?,?,'claimed',?,?)"
                " ON CONFLICT(publish_key) DO NOTHING",
                (key, platform, account, sha, title, now, now))
        conn.commit()
        if cur.rowcount != 1:
            raise PublishBlocked(f"{platform}/{account} 占位失败（并发抢占，key={key}）")
    finally:
        conn.close()
    return key, {"platform": platform, "account": account,
                 "video_sha256": sha, "title": title}


def mark_published(key: str, post_id: str = "", *, path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.execute("UPDATE publish_ledger SET status='published', post_id=?, updated_at=?"
                     " WHERE publish_key=?", (post_id or "", _now(), key))
        conn.commit()
    finally:
        conn.close()


def mark_failed(key: str, reason: str = "", *, path: Path | None = None) -> None:
    """发布失败 → 标 failed（下次可重试），不要留下 claimed 挡路。"""
    conn = connect(path)
    try:
        conn.execute("UPDATE publish_ledger SET status='failed', reason=?, updated_at=?"
                     " WHERE publish_key=?", (reason[:500], _now(), key))
        conn.commit()
    finally:
        conn.close()


def mark_deleted(key: str, *, path: Path | None = None) -> None:
    """平台上人工删除了 → 允许以后重发同一视频。"""
    conn = connect(path)
    try:
        conn.execute("UPDATE publish_ledger SET status='deleted', updated_at=? WHERE publish_key=?",
                     (_now(), key))
        conn.commit()
    finally:
        conn.close()


def release(key: str, *, path: Path | None = None) -> bool:
    """清掉未收尾的 claimed 占位（仅在确认上次没发出去时使用）。"""
    conn = connect(path)
    try:
        cur = conn.execute("DELETE FROM publish_ledger WHERE publish_key=? AND status='claimed'", (key,))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def list_rows(platform: str | None = None, limit: int = 20,
              path: Path | None = None) -> list[dict[str, Any]]:
    conn = connect(path)
    try:
        sql = ("SELECT publish_key, platform, account, status, post_id, title, updated_at"
               " FROM publish_ledger")
        args: list[Any] = []
        if platform:
            sql += " WHERE platform=?"
            args.append(platform)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        cols = ("publish_key", "platform", "account", "status", "post_id", "title", "updated_at")
        return [dict(zip(cols, r)) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _cli() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="发布台账查看/清理")
    ap.add_argument("--list", nargs="?", const="", metavar="PLATFORM", help="列出台账（可按平台过滤）")
    ap.add_argument("--release", metavar="KEY", help="清掉未收尾的 claimed 占位")
    ap.add_argument("--deleted", metavar="KEY", help="标记该条已被平台删除（允许重发）")
    ap.add_argument("--db", help="指定数据库路径（默认 SQLITE_DB_PATH 或 data/pipeline_state.db）")
    a = ap.parse_args()

    p = Path(a.db) if a.db else None
    if a.release:
        print("已释放" if release(a.release, path=p) else "没有可释放的 claimed 行:", a.release)
        return 0
    if a.deleted:
        mark_deleted(a.deleted, path=p)
        print("已标记 deleted:", a.deleted)
        return 0
    rows = list_rows(a.list or None, 50, path=p)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
