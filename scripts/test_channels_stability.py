#!/usr/bin/env python3
"""视频号稳定性 P0 回归（T1 错误码/强制新开 · T2 扫码即返回 · T3 登录态 fail-closed）。

全程离线：浏览器用替身，飞书不配置，台账用临时库。
用法：python3 scripts/test_channels_stability.py [-v]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

VERBOSE = "-v" in sys.argv
RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    if VERBOSE or not cond:
        print(f"{'PASS' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


# ------------------------------------------------------------------ T1
def test_t1_error_codes() -> None:
    import src.providers.channels_session as cs

    err = cs.ChannelsSessionError("随便一个消息")
    check("T1 默认 code=CDP_UNAVAILABLE", err.code == cs.ERR_CDP_UNAVAILABLE, err.code)
    busy = cs.ChannelsSessionError("占用", code=cs.ERR_PROFILE_BUSY)
    check("T1 可显式传 code", busy.code == "PROFILE_BUSY", busy.code)

    # 复用路径：常驻在跑 → ensure() 直接返回 CDP_URL，不新开
    orig_probe, orig_launch = cs.probe, cs.launch
    cs.probe = lambda timeout=1.5: {"Browser": "Chrome/test"}     # type: ignore
    launched: list[str] = []
    cs.launch = lambda **k: launched.append("launch")             # type: ignore
    check("T1 常驻在跑 → ensure() 复用（不 launch）",
          cs.ensure() == cs.CDP_URL and launched == [], str(launched))

    # --no-reuse-cdp：强制新开，但 9334 有实例 → PROFILE_BUSY，且不含 Chromium 栈
    cs.lock_holder = lambda: (12625, True, True)                  # type: ignore
    code, msg = "", ""
    try:
        cs.ensure(reuse=False)
    except cs.ChannelsSessionError as exc:
        code, msg = exc.code, str(exc)
    check("T1 --no-reuse-cdp + 常驻在跑 → PROFILE_BUSY", code == "PROFILE_BUSY", code)
    check("T1 消息给三条处理建议、无 Chromium 栈",
          "处理办法" in msg and "SingletonLock" not in msg and "launch_persistent_context" not in msg,
          msg[:80])
    check("T1 强制新开时没有真的去 launch", launched == [], str(launched))

    # launch 失败（超时/端口被占）→ CDP_UNAVAILABLE
    cs.launch = orig_launch          # 先把上面的替身还原，否则测的是替身
    cs.probe = lambda timeout=1.5: None                            # type: ignore
    cs.lock_holder = lambda: None                                  # type: ignore
    cs._clear_stale_lock = lambda: None                             # type: ignore
    cs.USER_DATA_DIR = Path("/tmp")                                 # 避免 mkdir 副作用
    orig_popen = cs.subprocess.Popen

    class _FakeProc:
        pid = 999999

        def poll(self):
            return 1        # 立刻退出 → launch 走失败分支

    cs.subprocess.Popen = lambda *a, **k: _FakeProc()               # type: ignore
    cs.LAUNCH_LOG = Path(tempfile.mkdtemp()) / "launch.log"
    code2 = ""
    try:
        cs.launch(timeout=0.5)
    except cs.ChannelsSessionError as exc:
        code2 = exc.code
    check("T1 启动失败 → CDP_UNAVAILABLE", code2 == "CDP_UNAVAILABLE", code2)

    cs.subprocess.Popen = orig_popen
    cs.probe, cs.launch = orig_probe, orig_launch


# ------------------------------------------------------------------ T2
def test_t2_wait_returns_on_scan() -> None:
    """扫码成功后 ≤20s 返回 0（旧实现只盯自己开的页面，URL 不变就等满 5 分钟）。"""
    sys.path.insert(0, str(PROJ / "scripts"))
    import channels_login as cl

    class _Page:
        def __init__(self, url: str, logged: bool) -> None:
            self.url = url
            self._logged = logged

    class _Ctx:
        def __init__(self, pages) -> None:
            self.pages = pages

    # ① 扫码后「另一个 tab」变成已登录 → 3s 内就应判定成功（不需要等主页复验）
    page = _Page("https://channels.weixin.qq.com/platform/post/create", False)
    other = _Page("https://channels.weixin.qq.com/platform", False)
    ctx = _Ctx([page, other])

    orig_ilogged = cl.is_logged_in

    async def fake_is_logged_in(p):
        return p is other and getattr(other, "_logged", False)

    cl.is_logged_in = fake_is_logged_in                        # type: ignore

    async def flip_after_one_round():
        await asyncio.sleep(0.05)
        other._logged = True

    async def driver():
        flip = asyncio.create_task(flip_after_one_round())
        res = await cl.wait_for_login(ctx, page, timeout_sec=60, poll=0.05,
                                     full_check=15, verify=_never)
        await flip
        return res

    async def _never(url):
        return False, ""

    ok, waited, how = asyncio.run(driver())
    check("T2 扫码后命中其它 tab → 立即返回成功", ok is True, f"{ok} {how}")
    # 2026-11-20：tab 命中不再直接采信，必须过服务端复验，判定方式随之变成证据串
    check("T2 判定方式带服务端复验证据", "服务端复验" in how, how)
    check("T2 不需要等满超时（耗时 < 15s）", waited < 15, f"{waited}s")

    # ② 页面 URL 不变（SPA）→ 靠 15s 主页复验也能判出来
    page2 = _Page("https://channels.weixin.qq.com/platform/post/create", False)
    ctx2 = _Ctx([page2])
    calls = {"n": 0}

    async def fake_is_logged_in2(p):
        return False

    async def verify_ok(url):
        calls["n"] += 1
        return True, "https://channels.weixin.qq.com/platform"

    cl.is_logged_in = fake_is_logged_in2                   # type: ignore
    ok2, waited2, how2 = asyncio.run(
        cl.wait_for_login(ctx2, page2, timeout_sec=60, poll=0.05, full_check=0.1,
                          verify=verify_ok))
    check("T2 SPA 场景靠主页复验判出", ok2 is True and "主页复验" in how2, how2)
    check("T2 主页复验被调用", calls["n"] >= 1, str(calls))

    cl.is_logged_in = orig_ilogged


# ------------------------------------------------------------------ T3
def test_t3_fail_closed(tmp: Path) -> None:
    import src.pipeline.preflight as pf

    orig_logged, orig_probe = pf._channels_logged_in, pf.CHANNELS_CDP
    pf.CHANNELS_CDP = "http://127.0.0.1:9334"

    # ⓐ 快照文件不存在也不再影响判定（旧行为：CHANNELS_LOGIN_MISSING 直接拦）
    pf.CHANNELS_STATE = tmp / "definitely_missing.json"
    pf._channels_logged_in = lambda: (True, "后台可见")       # type: ignore
    r = pf.probe_channels()
    check("T3 不再拿 storage_state 当判据（快照缺失也能通过）",
          r["ok"] is True and r["code"] == "", f"{r['ok']}/{r['code']}")

    # ⓑ 判不了 → fail-closed（旧行为：放行 = 等于没有门禁）
    pf._channels_logged_in = lambda: (None, "页面特征不明确")  # type: ignore
    r = pf.probe_channels()
    check("T3 登录态判不出 → CHANNELS_LOGIN_UNVERIFIED 且拦",
          r["ok"] is False and r["code"] == "CHANNELS_LOGIN_UNVERIFIED",
          f"{r['ok']}/{r['code']}")
    check("T3 拦截消息带扫码命令", "channels_login.py" in r["fix"], r["fix"][:50])

    # ⓒ 重试：前两次判不出、第三次成功 → 放行（避免偶发抖动误伤）
    seq = [(None, "抖动"), (None, "抖动"), (True, "后台可见")]

    def flaky():
        return seq.pop(0)

    pf._channels_logged_in = flaky                            # type: ignore
    r = pf.probe_channels()
    check("T3 三次重试内判定成功 → 放行", r["ok"] is True and r["code"] == "", f"{r['ok']}/{r['code']}")

    # ⓓ 明确未登录 → 保持原行为
    pf._channels_logged_in = lambda: (False, "页面含登录视频号助手")   # type: ignore
    r = pf.probe_channels()
    check("T3 明确未登录 → CHANNELS_NOT_LOGGED_IN",
          r["ok"] is False and r["code"] == "CHANNELS_NOT_LOGGED_IN", r["code"])

    pf._channels_logged_in, pf.CHANNELS_CDP = orig_logged, orig_probe


def test_t3_publish_blocked_no_dirty_ledger(tmp: Path) -> None:
    """判不了 → 发布被拦，且台账**没有**脏记录（claim 都没占）。"""
    import sqlite3

    from src.pipeline import executors as ex
    import src.pipeline.preflight as pf

    run = tmp / "run"
    run.mkdir(parents=True)
    (run / "draft_script.json").write_text(
        json.dumps({"title": "视频号门禁自检", "video_desc": "正文", "tags": ["x"]},
                   ensure_ascii=False), encoding="utf-8")
    (run / "video.mp4").write_bytes(b"f")
    (run / "cover.png").write_bytes(b"f")
    (run / "approval.json").write_text(
        json.dumps({"decision": "approve", "decided_by": "test"}), encoding="utf-8")

    db = tmp / "state.db"
    called: list[str] = []
    orig_invoke, orig_probe_all = ex._invoke_provider, pf.probe_all
    ex._invoke_provider = lambda *a, **k: called.append("p") or {"success": True}   # type: ignore
    pf.probe_all = lambda plats: [{"platform": "channels", "ok": False,
                                   "code": "CHANNELS_LOGIN_UNVERIFIED",
                                   "detail": "登录态无法验证（重试 3 次仍判不出）",
                                   "fix": "python3 scripts/channels_login.py --qr-out /tmp/qr.png"}]  # type: ignore

    ctx = ex.StepContext(step={"id": "publish", "params": {"enabled": True,
                                                          "platforms": ["channels"],
                                                          "dry_run": False}},
                         run_root=run, run_id="vstab-selfcheck", task_id="t",
                         material=None, db_path=str(db))
    raised = ""
    try:
        ex.exec_publish(ctx)
    except ex.ExecutorError as exc:
        raised = str(exc)
    check("T3 判不了 → 发布抛 ExecutorError", raised.startswith("发布前置预检未通过"), raised[:60])
    check("T3 判不了 → 没进发布循环", called == [], str(called))

    rows = 0
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("select count(*) from publish_ledger").fetchone()[0]
        except sqlite3.Error:
            rows = 0
        finally:
            conn.close()
    check("T3 判不了 → 台账无脏记录（0 行）", rows == 0, f"{rows} 行")

    ex._invoke_provider, pf.probe_all = orig_invoke, orig_probe_all


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        test_t1_error_codes()
        test_t2_wait_returns_on_scan()
        test_t3_fail_closed(Path(d))
        test_t3_publish_blocked_no_dirty_ledger(Path(d))
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, det) for l, c, det in RESULTS if not c]
    print(f"\nCHANNELS STABILITY {ok} PASS / {len(bad)} FAIL")
    for l, det in bad:
        print(f"  ✗ {l} {det}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
