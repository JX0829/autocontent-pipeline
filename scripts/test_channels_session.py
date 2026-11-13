#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""channels_session 回归测试（2026-11-12 视频号 CDP 会话统一改造）

不碰生产 profile / 生产端口：全部用临时目录 + 9336/9337/9338，真起真停 Chrome。

覆盖：
  1. probe: 没有 CDP 时返回 None
  2. ensure: 冷启动拉起 Chrome → probe 有值，SingletonLock 指向活进程且确实在用该目录
  3. 复用: 再调一次 ensure 不重启浏览器（pid 不变）
  4. 真占用: 进程 cmdline 里带同一 user-data-dir、且该端口没有 CDP → 抛
     ChannelsSessionError，提示含「占用」+ pid + 可执行办法
  5. 假占用: 拷贝出来的 profile 里留着原机 pid 的 SingletonLock（进程活着但没在用
     这份目录）→ **不**误判为占用，照常拉起
  6. stop: 收尾后 probe 变 None
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="chan_session_test_"))
PORT = 9336
PORT_BUSY = 9337
PORT_COPY = 9338

os.environ["CHANNELS_PROFILE_DIR"] = str(TMP / "profile")
os.environ["CHANNELS_CDP_PORT"] = str(PORT)
os.environ.pop("CHANNELS_CDP_URL", None)
sys.path.insert(0, str(PROJ))

from src.providers import channels_session as cs  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}{(' | ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def _point_at(profile_dir: Path, port: int):
    """把模块指向另一个 profile/端口，返回还原函数。"""
    saved = (cs.USER_DATA_DIR, cs.CDP_PORT, cs.CDP_URL, cs.PROFILE_ROOT)
    cs.PROFILE_ROOT = profile_dir
    cs.USER_DATA_DIR = profile_dir / "chrome_profile"
    cs.CDP_PORT = port
    cs.CDP_URL = f"http://127.0.0.1:{port}"

    def restore():
        cs.USER_DATA_DIR, cs.CDP_PORT, cs.CDP_URL, cs.PROFILE_ROOT = saved
    return restore


def main() -> int:
    print(f"临时 profile: {TMP}  端口 {PORT}")

    # 1. 冷启动前没有 CDP
    check("probe 冷启动为 None", cs.probe() is None, str(cs.probe()))

    # 2. ensure 拉起
    t0 = time.time()
    url = cs.ensure(headless=True, timeout=30)
    cost = time.time() - t0
    version = cs.probe() or {}
    check("ensure 返回固定端口地址", url == f"http://127.0.0.1:{PORT}", url)
    check("probe 拿到 browser 版本", bool(version.get("Browser")),
          f"{version.get('Browser')} in {cost:.1f}s")
    holder = cs.lock_holder()
    check("SingletonLock 指向活进程且在用该目录", bool(holder and holder[1] and holder[2]),
          str(holder))
    pid_first = holder[0] if holder else -1

    # 3. 复用：第二次 ensure 不重启
    cs.ensure(headless=True)
    holder2 = cs.lock_holder()
    check("第二次 ensure 复用同一浏览器", bool(holder2) and holder2[0] == pid_first,
          f"{pid_first} -> {holder2[0] if holder2 else None}")

    # 4. 真占用：cmdline 里带同一 user-data-dir 的活进程
    busy_dir = TMP / "busy_profile"
    busy_ud = busy_dir / "chrome_profile"
    busy_ud.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                             f"--user-data-dir={busy_ud}"])
    os.symlink(f"testhost-{proc.pid}", busy_ud / "SingletonLock")
    restore = _point_at(busy_dir, PORT_BUSY)
    try:
        cs.ensure(headless=True, timeout=5)
        check("真占用时抛 ChannelsSessionError", False, "没有抛异常")
    except cs.ChannelsSessionError as exc:
        msg = str(exc)
        check("真占用时抛 ChannelsSessionError", True)
        check("提示含「占用」与 pid", "占用" in msg and str(proc.pid) in msg, msg.splitlines()[0])
        check("提示给出可执行办法", "--stop" in msg and "kill" in msg)
    finally:
        proc.kill()
        restore()

    # 5. 假占用：拷贝出来的 profile，锁指向无关活进程
    copy_dir = TMP / "copied_profile"
    copy_ud = copy_dir / "chrome_profile"
    copy_ud.mkdir(parents=True, exist_ok=True)
    unrelated = subprocess.Popen(["sleep", "60"])
    os.symlink(f"testhost-{unrelated.pid}", copy_ud / "SingletonLock")
    restore = _point_at(copy_dir, PORT_COPY)
    try:
        cs.ensure(headless=True, timeout=30)
        check("拷贝 profile 的旧锁不误判占用", cs.probe() is not None,
              f"pid={unrelated.pid} 仍在，但目录不是它占的")
        check("旧锁已被清理", not (copy_ud / "SingletonLock").exists())
    except cs.ChannelsSessionError as exc:
        check("拷贝 profile 的旧锁不误判占用", False, str(exc).splitlines()[0])
        check("旧锁已被清理", False)
    finally:
        restore()
        cs_holder = copy_ud / "chrome_profile"
        for pid in subprocess.run(["pgrep", "-f", f"user-data-dir={cs_holder}"],
                                  capture_output=True, text=True).stdout.split():
            try:
                os.kill(int(pid), 15)
            except OSError:
                pass
        unrelated.kill()

    # 6. stop 收尾（主 profile）
    acted, msg = cs.stop()
    time.sleep(1.5)
    check("stop 结束浏览器", acted and cs.probe() is None, msg)

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败"
          + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
