#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号登录「假成功」回归（2026-11-20 P0）。

事故：页面停在 login.html，脚本打印「✅ 登录成功（3s 内确认）」并 exit 0。
本测试锁死三条硬证据：
  ① URL 在登录页 → 必须 False（本次事故的直接判据）
  ② 登录页文案（扫码登录/二维码已失效）→ False
  ③ 没有后台特征文案 → False（不能因为 URL 像就放行）
  ④ 真后台页（/platform + 「发表视频」）→ True
  ⑤ 过期 cookie 不算证据：login.html 即便 sessionid/wxuin 还在，仍必须 False
"""
import asyncio
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.providers.channels_session import is_logged_in, login_evidence  # noqa: E402

PASS = 0
FAIL = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"PASS  {name}" + (f" | {extra}" if extra else ""))
    else:
        FAIL.append(name)
        print(f"FAIL  {name}" + (f" | {extra}" if extra else ""))


class FakePage:
    def __init__(self, url: str, body: str):
        self.url = url
        self._body = body

    async def inner_text(self, sel: str) -> str:
        return self._body


async def main() -> int:
    ok, why = await login_evidence(FakePage(
        "https://channels.weixin.qq.com/login.html", "登录视频号助手 扫码登录"))
    check("① login.html 必须判未登录", not ok, why)

    ok, why = await login_evidence(FakePage(
        "https://channels.weixin.qq.com/platform", "登录视频号助手 发表视频"))
    check("② 后台 URL 但出现扫码文案 → 仍判未登录", not ok, why)

    ok, why = await login_evidence(FakePage(
        "https://channels.weixin.qq.com/platform", "空白页 加载中"))
    check("③ 无后台特征文案 → 未登录", not ok, why)

    ok, why = await login_evidence(FakePage(
        "https://channels.weixin.qq.com/platform", "发表视频 内容管理 视频号ID"))
    check("④ 真后台页 → 已登录", ok, why)
    check("④ 证据串含命中项", "命中" in why, why)

    # ⑤ 过期 cookie 场景：cookie 不参与判定，login.html 一律 False
    ok, why = await login_evidence(FakePage(
        "https://channels.weixin.qq.com/login.html", "发表视频 内容管理"))
    check("⑤ login.html 即使 DOM 有后台文案也判未登录", not ok, why)

    ok = await is_logged_in(FakePage("https://channels.weixin.qq.com/login.html", "发表视频"))
    check("⑥ is_logged_in 与 login_evidence 口径一致", ok is False, str(ok))

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
