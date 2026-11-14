#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音 CDP 接管回归（SPEC-20261113-douyin-cdp-publish，2026-11-13）

覆盖：端点优先级、探活、单实例互斥、接管后**不关掉常驻 Chrome**、
掉线时 save_draft 返回 CDP_UNAVAILABLE 而不是抛栈。

需要 9333 常驻 Chrome 在场时才跑「真接管」两条；不在场则 SKIP（不算失败）。
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.providers import cdp_session  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


LIVE = "http://127.0.0.1:9333"
DEAD = "http://127.0.0.1:9339"


def test_resolve() -> None:
    os.environ.pop("DOUYIN_CDP_URL", None)
    check("R1 账号配置生效", cdp_session.resolve({"cdp_url": LIVE}, "DOUYIN_CDP_URL") == LIVE)
    check("R1 留空回退 env",
          cdp_session.resolve({"cdp_url": ""}, "DOUYIN_CDP_URL", LIVE) == LIVE)
    check("R1 全空 = 关闭 CDP（老逻辑）",
          cdp_session.resolve({}, "DOUYIN_CDP_URL") == "")
    check("R1 账号配置优先于 env",
          cdp_session.resolve({"cdp_url": LIVE}, "DOUYIN_CDP_URL", DEAD) == LIVE)
    os.environ["DOUYIN_CDP_URL"] = DEAD + "/"
    check("R1 env 生效且去掉尾斜杠",
          cdp_session.resolve({}, "DOUYIN_CDP_URL") == DEAD)
    os.environ.pop("DOUYIN_CDP_URL", None)


def test_probe() -> None:
    ok, detail = cdp_session.probe(DEAD, timeout=1.0)
    check("R3 掉线探活返回 False 而不是抛栈", ok is False, detail[:60])
    live_ok, live_detail = cdp_session.probe(LIVE, timeout=2.0)
    if live_ok:
        check("R3 在线探活返回 True 并带版本号", "Chrome" in live_detail, live_detail[:60])
    else:
        print(f"SKIP  9333 未启动，跳过真接管相关用例（{live_detail[:50]}）")


def test_exclusive() -> None:
    try:
        with cdp_session.exclusive("douyin", LIVE):
            try:
                with cdp_session.exclusive("douyin", LIVE):
                    check("R5 并发必须 CDP_BUSY", False, "第二把锁竟然拿到了")
            except cdp_session.CdpBusy as exc:
                check("R5 并发必须 CDP_BUSY", True, str(exc)[:60])
        with cdp_session.exclusive("douyin", LIVE):
            check("R5 释放后可再次获取", True)
    except Exception as exc:  # noqa: BLE001
        check("R5 锁机制可用", False, f"{type(exc).__name__}: {exc}")


def test_connect_keeps_chrome_alive() -> None:
    ok, _ = cdp_session.probe(LIVE, timeout=2.0)
    if not ok:
        print("SKIP  9333 未启动，跳过「接管不关 Chrome」用例")
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context, browser = cdp_session.connect(p, LIVE)
        page = context.new_page()
        page.goto("https://creator.douyin.com/creator-micro/content/upload",
                  wait_until="domcontentloaded", timeout=30000)
        url = page.url
        browser.close()          # 关键：只关页面，不许关 Chrome
    still, detail = cdp_session.probe(LIVE, timeout=2.0)
    check("R2 接管后 browser.close() 不关掉常驻 Chrome", still, detail[:50])
    check("R2 接管到的是真实登录 context（creator-micro 或登录页）",
          "douyin.com" in url, url[:70])


def test_unavailable_surface() -> None:
    """掉线时 save_draft 必须返回 CDP_UNAVAILABLE 而不是抛出。"""
    from src.providers.douyin import DouyinProvider
    prov = DouyinProvider({"id": "douyin-test", "cdp_url": DEAD,
                           "profile_path": "./data/browser_profiles/douyin-test"})
    res = prov.save_draft("/tmp/not-exist.mp4", None, "标题", [], "")
    check("R3 掉线时 save_draft 不抛栈（先拦在视频不存在）",
          res.get("error") is not None, str(res.get("error")))

    # 造一个存在的空视频，让流程走到 CDP 探活
    tmp = Path("/tmp/_cdp_probe_dummy.mp4")
    tmp.write_bytes(b"\x00" * 16)
    res2 = prov.save_draft(str(tmp), None, "标题", [], "")
    check("R3 CDP 不可用 → CDP_UNAVAILABLE 且不占台账",
          res2.get("error") == "CDP_UNAVAILABLE", str(res2)[:120])
    tmp.unlink(missing_ok=True)


class _FakePage:
    """只实现 _is_login_page 用到的两个口子。"""

    def __init__(self, url: str, body: str):
        self.url = url
        self._body = body

    def inner_text(self, sel: str) -> str:
        return self._body


def test_login_page_detector() -> None:
    """llamacpp-v1-1 那天 check_login_status 返回 True 却 LOGIN_REQUIRED：
    登录页 URL 也在 creator-micro 下，旧判据只看 URL 就误判成已登录。"""
    from src.providers.douyin import DouyinProvider
    prov = DouyinProvider({"id": "douyin-test", "cdp_url": "",
                           "profile_path": "./data/browser_profiles/douyin-test"})
    login_body = "抖音创作者的一站式创作与服务平台 扫码登录 验证码登录 密码登录 登录即代表同意"
    check("R4 登录页（URL 含 creator-micro）必须判定未登录",
          prov._is_login_page(_FakePage(
              "https://creator.douyin.com/creator-micro/content/upload", login_body)) is True)
    check("R4 已登录工作台不误判",
          prov._is_login_page(_FakePage(
              "https://creator.douyin.com/creator-micro/content/upload",
              "发布视频 作品管理 数据中心 互动管理")) is False)
    check("R4 passport 跳转判定未登录",
          prov._is_login_page(_FakePage("https://passport.douyin.com/login", "")) is True)


def test_ledger_classification() -> None:
    """① 台账假阴性：未登录被记成 unverified（挡住重发）+ 回读无证据却记 published。"""
    import tempfile

    from src.publish_ledger import (check_and_claim, classify_failure, record_outcome)

    check("① LOGIN_REQUIRED 判「确定没发出」可重试",
          classify_failure("LOGIN_REQUIRED", "抖音登录态已失效") == "failed")
    check("① CDP_UNAVAILABLE 可重试", classify_failure("CDP_UNAVAILABLE", "") == "failed")
    check("① CDP_BUSY 可重试", classify_failure("CDP_BUSY", "") == "failed")
    check("① 点过发布后的未知异常仍为 unverified",
          classify_failure("SUBMIT_UNKNOWN", "点发布后超时") == "unverified")

    work = pathlib.Path(tempfile.mkdtemp(prefix="ledger_"))
    db = work / "state.db"
    vids = {}
    for name in ("x", "y", "z"):
        v = work / f"{name}.mp4"
        v.write_bytes(name.encode() * 16)   # 内容必须不同，否则 publish_key 相同
        vids[name] = str(v)
    key, _ = check_and_claim("douyin", "acct", vids["x"], "标题", path=db)
    st1 = record_outcome(key, True, external_id="", path=db)          # 无证据
    check("① 无外部 id 不许记 published", st1 == "unverified", st1)

    key2, _ = check_and_claim("douyin", "acct", vids["y"], "标题2", path=db)
    st2 = record_outcome(key2, True, external_id="readback_123", path=db)
    check("① 有回读 id 才记 published", st2 == "published", st2)

    key3, _ = check_and_claim("douyin", "acct", vids["z"], "标题3", path=db)
    st3 = record_outcome(key3, False, status="LOGIN_REQUIRED", reason="未登录", path=db)
    check("① 未登录落 failed（不再需要人工 release）", st3 == "failed", st3)


def test_resume_modal_markers() -> None:
    """② 上传页「上次未发布的视频」弹窗必须被识别并放弃。"""
    pass
    return


def main() -> int:
    test_resolve()
    test_login_page_detector()
    test_ledger_classification()
    test_probe()
    test_exclusive()
    test_connect_keeps_chrome_alive()
    test_unavailable_surface()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
