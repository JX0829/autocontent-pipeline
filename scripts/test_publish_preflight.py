#!/usr/bin/env python3
"""发布前置预检（T2）+ 台账结账（T1）离线回归。

覆盖：
1. `probe_*` 的三态判定（CDP 死 / 登录态缺 / 正常）；
2. `probe_all` 把失败聚合成告警行，且**告警里带可直接粘贴的修复命令**；
3. `exec_publish` 在预检失败时：**不进入发布循环**、抛 ExecutorError、写 publish.json；
4. `src.publish_ledger --attach KEY POST_ID [--mark-published]`：
   不带 flag → 仍是 unverified（禁止自动重发）；带 flag → published 且挂上 post_id。

全程离线：预检用死端口 / 临时目录，台账用临时 sqlite，不发任何消息。
用法：python3 scripts/test_publish_preflight.py [-v]
"""
from __future__ import annotations

import json
import os
import subprocess
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


# ---------------------------------------------------------------- ① probe
def test_probes(tmp: Path) -> None:
    import src.pipeline.preflight as pf

    # 抖音：CDP 死端口
    os.environ["DOUYIN_CDP_URL"] = "http://127.0.0.1:59999"
    pf.DOUYIN_CDP = "http://127.0.0.1:59999"
    r = pf.probe_douyin()
    check("抖音 CDP 掉线 → DOUYIN_CDP_UNAVAILABLE",
          r["ok"] is False and r["code"] == "DOUYIN_CDP_UNAVAILABLE", str(r["code"]))
    check("抖音告警带启动 Chrome 命令", "--remote-debugging-port=9333" in r["fix"], r["fix"][:60])

    # 抖音：CDP 正常但 profile 缺
    pf._probe_cdp = lambda url, timeout=2.0: {"Browser": "Chrome/test"}   # type: ignore
    pf.DOUYIN_PROFILE = tmp / "no_such_profile"
    r = pf.probe_douyin()
    check("抖音 profile 缺 → DOUYIN_PROFILE_MISSING",
          r["ok"] is False and r["code"] == "DOUYIN_PROFILE_MISSING", str(r["code"]))
    pf.DOUYIN_PROFILE = tmp / "profile"
    pf.DOUYIN_PROFILE.mkdir()
    check("抖音 CDP+profile 齐 → ok", pf.probe_douyin()["ok"] is True)

    # 视频号：CDP 死 / 登录态缺 / 正常
    import src.providers.channels_session as cs
    orig_probe = cs.probe
    cs.probe = lambda timeout=1.5: None            # type: ignore
    r = pf.probe_channels()
    check("视频号 CDP 掉线 → CHANNELS_CDP_UNAVAILABLE",
          r["ok"] is False and r["code"] == "CHANNELS_CDP_UNAVAILABLE", str(r["code"]))
    check("视频号告警带扫码命令", "channels_login.py" in r["fix"], r["fix"][:60])
    cs.probe = lambda timeout=1.5: {"Browser": "ok"}   # type: ignore
    pf.CHANNELS_STATE = tmp / "no_state.json"
    r = pf.probe_channels()
    check("视频号登录态缺 → CHANNELS_LOGIN_MISSING",
          r["ok"] is False and r["code"] == "CHANNELS_LOGIN_MISSING", str(r["code"]))
    pf.CHANNELS_STATE = tmp / "state.json"
    pf.CHANNELS_STATE.write_text("{}", encoding="utf-8")
    check("视频号 CDP+登录态齐 → ok", pf.probe_channels()["ok"] is True)
    cs.probe = orig_probe

    # B站：无文件 / 坏 JSON / 无 SESSDATA / 正常
    pf.BILI_COOKIES = tmp / "none.json"
    check("B站无 cookie → BILI_NO_COOKIE", pf.probe_bilibili()["code"] == "BILI_NO_COOKIE")
    pf.BILI_COOKIES.write_text("{broken", encoding="utf-8")
    check("B站坏 JSON → BILI_COOKIE_BROKEN", pf.probe_bilibili()["code"] == "BILI_COOKIE_BROKEN")
    pf.BILI_COOKIES.write_text(json.dumps({"cookies": [{"name": "buvid3"}]}), encoding="utf-8")
    check("B站缺 SESSDATA → BILI_NO_SESSDATA", pf.probe_bilibili()["code"] == "BILI_NO_SESSDATA")
    pf.BILI_COOKIES.write_text(json.dumps({"cookies": [{"name": "SESSDATA"}]}), encoding="utf-8")
    check("B站 cookie 齐 → ok", pf.probe_bilibili()["ok"] is True)

    # 未知平台不拦但要留痕
    unknown = pf.probe_platform("kuaishou")
    check("未接入探活的平台不阻断（NO_PROBE）",
          unknown["ok"] is True and unknown["code"] == "NO_PROBE", str(unknown["code"]))


def test_alert_lines() -> None:
    from src.pipeline.preflight import alert_lines
    lines = alert_lines([
        {"platform": "channels", "code": "CHANNELS_CDP_UNAVAILABLE",
         "detail": "9334 无响应", "fix": "python3 scripts/channels_login.py --qr-out /tmp/qr.png"},
        {"platform": "bilibili", "code": "BILI_NO_COOKIE",
         "detail": "缺 cookie", "fix": "重新导出 cookie"},
    ])
    blob = "\n".join(lines)
    check("告警卡含平台与错误码", "channels" in blob and "CHANNELS_CDP_UNAVAILABLE" in blob, blob[:80])
    check("告警卡含修复命令", "channels_login.py" in blob and "重新导出 cookie" in blob)
    check("告警卡明确说未发布任何内容", "不发布任何内容" in blob, blob[-60:])


# ---------------------------------------------------------------- ② exec_publish 门禁
def test_publish_gate(tmp: Path) -> None:
    """预检失败 → 不进发布循环（provider 不被调用）、抛 ExecutorError、写 publish.json。"""
    from src.pipeline import executors as ex

    run_root = tmp / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "draft_script.json").write_text(
        json.dumps({"title": "预检门禁自检", "video_desc": "正文", "tags": ["测试"]},
                   ensure_ascii=False), encoding="utf-8")
    (run_root / "video.mp4").write_bytes(b"fake")
    (run_root / "cover.png").write_bytes(b"fake")
    (run_root / "approval.json").write_text(
        json.dumps({"decision": "approve", "decided_by": "test"}), encoding="utf-8")

    called: list[str] = []
    orig_invoke = ex._invoke_provider
    ex._invoke_provider = lambda *a, **k: called.append("provider") or {"success": True}  # type: ignore

    import src.pipeline.preflight as pf
    orig_probe_all = pf.probe_all
    pf.probe_all = lambda plats: [   # type: ignore
        {"platform": "channels", "ok": False, "code": "CHANNELS_CDP_UNAVAILABLE",
         "detail": "9334 无响应", "fix": "python3 scripts/channels_login.py --qr-out /tmp/qr.png"}
    ]

    ctx = ex.StepContext(step={"id": "publish", "params": {"enabled": True,
                                                          "platforms": ["channels"],
                                                          "dry_run": False}},
                         run_root=run_root, run_id="preflight-selfcheck",
                         task_id="t", material=None, db_path=str(tmp / "state.db"))
    raised = ""
    try:
        ex.exec_publish(ctx)
    except ex.ExecutorError as exc:
        raised = str(exc)
    except Exception as exc:  # noqa: BLE001 — 抛错类型也要如实报
        raised = f"WRONG_EXC {type(exc).__name__}: {exc}"
    check("预检失败抛 ExecutorError", raised.startswith("发布前置预检未通过"), raised[:80])
    check("预检失败时没进发布循环（provider 未被调用）", called == [], str(called))

    pub = run_root / "publish.json"
    check("publish.json 已落盘", pub.exists())
    if pub.exists():
        data = json.loads(pub.read_text(encoding="utf-8"))
        fails = data.get("failed") or []
        check("publish.json 记了 gate=preflight 的失败",
              any(f.get("gate") == "preflight" for f in fails), str(fails)[:120])
        check("publish.json 保留修复命令", any("channels_login.py" in str(f.get("fix") or "")
                                        for f in fails))

    ex._invoke_provider = orig_invoke
    pf.probe_all = orig_probe_all


# ---------------------------------------------------------------- ③ 台账结账
def test_ledger_mark_published(tmp: Path) -> None:
    db = tmp / "ledger.db"
    env = {**os.environ, "SQLITE_DB_PATH": str(db)}
    py = sys.executable

    sys.path.insert(0, str(PROJ))
    from src.publish_ledger import check_and_claim, list_rows, mark_unverified

    video = tmp / "v.mp4"
    video.write_bytes(b"x")
    key, _ = check_and_claim("douyin", "acct-test", video, "预检结账自检", path=db)
    mark_unverified(key, reason="有作品无标题", path=db)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([py, "-m", "src.publish_ledger", *args, "--db", str(db)],
                              cwd=str(PROJ), env=env, capture_output=True, text=True)

    r = run("--attach", key, "7708000000000000001")
    row = [x for x in list_rows("douyin", 50, path=db) if x["publish_key"] == key][0]
    check("只 --attach → 仍 unverified（禁自动重发）",
          row["status"] == "unverified" and row["post_id"] == "7708000000000000001",
          f"{row['status']}/{row['post_id']}")
    check("--attach 不带 flag 的提示语正确", "仍为 unverified" in r.stdout, r.stdout.strip()[:60])

    r2 = run("--attach", key, "7708000000000000001", "--mark-published")
    row = [x for x in list_rows("douyin", 50, path=db) if x["publish_key"] == key][0]
    check("--attach + --mark-published → published 且带 post_id",
          row["status"] == "published" and row["post_id"] == "7708000000000000001",
          f"{row['status']}/{row['post_id']}")
    check("结账提示语正确", "结账为 published" in r2.stdout, r2.stdout.strip()[:60])

    # 幂等：对已 published 的行再结账，应报「没有可挂的 unverified 行」且 rc=1
    r3 = run("--attach", key, "7708000000000000001", "--mark-published")
    check("对非 unverified 行结账 → rc=1 且不改状态",
          r3.returncode == 1 and "没有可挂的 unverified 行" in r3.stdout,
          f"rc={r3.returncode} {r3.stdout.strip()[:50]}")


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_probes(tmp)
        test_alert_lines()
        test_publish_gate(tmp)
        test_ledger_mark_published(tmp)
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, det) for l, c, det in RESULTS if not c]
    print(f"\nPREFLIGHT+LEDGER {ok} PASS / {len(bad)} FAIL")
    for l, det in bad:
        print(f"  ✗ {l} {det}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
