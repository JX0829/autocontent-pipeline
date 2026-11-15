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

# 这些状态码/原因代表「平台侧确定没收到这条内容」，可以安全重试。
# 反例（务必别加进来）：PUBLISHED_UNVERIFIED、submit 之后的超时/异常、
# 任何"点了发布但没看到结果"的情况 —— 那些标 unverified，禁止自动重发。
SAFE_RETRY_SIGNALS = (
    "LOGIN_EXPIRED",             # 未登录，根本没进提交
    "LOGIN_REQUIRED",            # 同上，provider 用的是这个词（2026-11-14 补：原先漏了，
                                 # 于是「未登录」被记成 unverified，还得人工 release 才敢重发）
    "CDP_UNAVAILABLE",           # 常驻 Chrome 掉线，连页面都没打开
    "CDP_BUSY",                  # 端口被别人占着，本流程根本没开始
    "ORIGINAL_CHECKBOX_NOT_FOUND",  # 找不到原创勾选，停在提交前
    "NOT_SUPPORTED_ON_WEB",
    "STOPPED_BEFORE_PUBLISH",    # 故意停在发表前
    "PREFLIGHT",                 # 上传页准备失败（未点「发布」）
    "UPLOAD_PAGE_NOT_READY",
    "DRAFT_BUTTON_NOT_FOUND",    # 找不到草稿按钮，安全策略主动拦停
    "TITLE_NOT_LANDED",          # 2026-11-15：标题没落进「作品标题」框 → 未点发布，可重试
    "CAPTION_MISMATCH",          # 正文回读与预期不符 → 未点发布，可重试
    "SUBMIT_BLOCKED",            # 2026-11-15：点发布前自证不过（标题/正文读回为空）
    "CAPTION_NOT_LANDED",        # CLI：正文没落上，未点发布
    "TITLE_NOT_LANDED_CLI",      # 预留：CLI 侧标题失败别名
    "DRY_RUN",
    "MISSING_PARAM",
    "VALIDATION",
    "FILE_NOT_FOUND",
    "MATERIAL_READY",            # 素材就绪但未提交
)

BLOCKING_STATUSES = ("published", "claimed", "unverified")


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
        if row and row[0] in BLOCKING_STATUSES:
            if row[0] == "published":
                raise PublishBlocked(
                    f"{platform}/{account} 已发布过同一视频（post_id={row[1] or '未知'}，时间 {row[2]}）")
            if row[0] == "unverified":
                raise PublishBlocked(
                    f"{platform}/{account} 上次发布结果未知（unverified @ {row[2]}）——"
                    f"平台可能已经收到这条，先去后台核对；确认没发出再清理："
                    f"python3 -m src.publish_ledger --release {key}")
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


def mark_unverified(key: str, reason: str = "", *, external_id: str = "",
                    path: Path | None = None) -> None:
    """结果未知 → 标 unverified：占位保留，**禁止自动重发**，等人工去平台核对。

    典型场景：点了"发表"之后超时/异常、页面报 PUBLISHED_UNVERIFIED。
    这类不能标 failed —— failed 允许重试，而"可能已经发出去"的情况下重试就是重复发布。

    `external_id`：2026-11-15 起允许挂上回读到的作品 id（"有作品无标题"场景）——
    状态仍是 unverified（不许自动重发），但人工补标题时能直接定位是哪一个作品。
    """
    conn = connect(path)
    try:
        conn.execute(
            "UPDATE publish_ledger SET status='unverified', reason=?,"
            " post_id=COALESCE(NULLIF(?, ''), post_id), updated_at=?"
            " WHERE publish_key=?",
            (reason[:500], str(external_id or "").strip(), _now(), key))
        conn.commit()
    finally:
        conn.close()


def classify_failure(status: str = "", reason: str = "") -> str:
    """判断这次失败是「确定没发出去」(failed，可重试) 还是「结果未知」(unverified，禁止重发)。

    默认 **unverified** —— 宁可拦住让人核对，也不自动重发。只有命中 SAFE_RETRY_SIGNALS
    的明确前置失败才允许重试。
    """
    blob = f"{status} {reason}".upper()
    if any(sig in blob for sig in SAFE_RETRY_SIGNALS):
        return "failed"
    return "unverified"


def record_outcome(key: str, published: bool, *, external_id: str = "",
                   status: str = "", reason: str = "",
                   path: Path | None = None) -> str:
    """统一收尾入口：成功 → published；确定没发出 → failed；其余 → unverified。

    返回值是最终落库的 status，方便 CLI 打印如实结果。
    """
    if published:
        # 2026-11-14：**没有外部 id 就不算 published**。ds-coding-plan-1 留下过一条
        # status=published 但 post_id 为空的记录（回读没拿到证据却记成功），
        # 既挡不住重复投稿，也说不清到底发没发。没证据 → unverified，等人工核对。
        # 2026-11-15 追加：`manual_verified_*` 这类人手写占位 id 同样不算证据
        # —— 当时把「回读未命中、判定未发布」的视频写成了 published，
        # 平台上根本没有这条作品，台账却挡住了重发。
        _eid = str(external_id or "").strip()
        if not _eid or _eid.startswith("manual_verified") or _eid.startswith("manual_"):
            mark_unverified(key, f"回读未拿到外部 id（{status or 'no_evidence'}）", path=path)
            return "unverified"
        mark_published(key, external_id, path=path)
        return "published"
    detail = (reason or status or "unknown")[:500]
    if classify_failure(status, reason) == "failed":
        mark_failed(key, detail, path=path)
        return "failed"
    mark_unverified(key, detail, external_id=external_id, path=path)
    return "unverified"


def attach_post_id(key: str, post_id: str, *, path: Path | None = None) -> bool:
    """给 unverified 行补上平台作品 id（不改变 status，仍是 unverified）。

    用途：`PUBLISHED_NO_TITLE` / 回读漏判导致「作品在、台账不知道」时，
    人工核对平台后把 id 挂上，避免以后再被判成"没发出去"重复投稿。
    """
    conn = connect(path)
    try:
        cur = conn.execute(
            "UPDATE publish_ledger SET post_id=?, updated_at=?"
            " WHERE publish_key=? AND status='unverified'",
            (str(post_id).strip(), _now(), key))
        conn.commit()
        return cur.rowcount > 0
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
    """清掉未收尾的占位（claimed / unverified）。仅在人工确认上次没发出去时使用。"""
    conn = connect(path)
    try:
        cur = conn.execute(
            "DELETE FROM publish_ledger WHERE publish_key=? AND status IN ('claimed','unverified')",
            (key,))
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
    ap.add_argument("--attach", nargs=2, metavar=("KEY", "POST_ID"),
                    help="给 unverified 行挂上平台作品 id（「有作品无标题」人工补标题后用）")
    ap.add_argument("--mark-published", action="store_true",
                    help="配合 --attach：人工在平台补完标题/确认作品已发出后，"
                         "把该行结账为 published（不再挂 unverified）")
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
    if a.attach:
        key, post_id = a.attach
        hit = attach_post_id(key, post_id, path=p)
        if not hit:
            print("没有可挂的 unverified 行:", key)
            return 1
        if a.mark_published:
            mark_published(key, post_id, path=p)
            print("已挂上 post_id 并结账为 published:", key, post_id)
        else:
            print("已挂上 post_id（仍为 unverified，禁止自动重发）:", key)
        return 0
    rows = list_rows(a.list or None, 50, path=p)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
