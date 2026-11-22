#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号 post_list「查不了」回归（2026-11-20 P0）。

现场描述：`/platform/post/list` 主帧正文只有 11 字符、iframe 空 → 结论「查不了」。
真机实测：正文确实 11 字符，但**接口 errCode=0、返回 5 条 20KB JSON**（主帧和
iframe 都能调通）。所以证据必须来自接口，不能拿 DOM 正文猜。

本测试锁死：
  ① 主帧可用 → 直接返回数据
  ② 主帧抛错 → 自动退到 iframe 并成功
  ③ 两处都失败 → 抛 ChannelsListError（绝不返回空列表冒充「没有作品」）
"""
import asyncio
import json
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.providers.channels_edit import ChannelsListError, fetch_post_list  # noqa: E402

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


OK_BODY = json.dumps({"errCode": 0, "data": {"list": [{"objectId": "export/abc"}]}})


class Frame:
    def __init__(self, url: str, behaviour: str):
        self.url = url
        self.behaviour = behaviour

    async def evaluate(self, js, arg=None):
        if self.behaviour == "ok":
            return json.dumps({"__status": 201, "__body": OK_BODY})
        if self.behaviour == "empty":
            return json.dumps({"__status": 201, "__body": json.dumps({"errCode": 0, "data": {"list": []}})})
        raise RuntimeError("fetch failed")


class Page:
    def __init__(self, behaviour: str, frames=()):
        self.behaviour = behaviour
        self.url = "https://channels.weixin.qq.com/platform/post/list"
        self.frames = [self] + list(frames)

    async def evaluate(self, js, arg=None):
        if self.behaviour == "ok":
            return json.dumps({"__status": 201, "__body": OK_BODY})
        if self.behaviour == "empty":
            return json.dumps({"__status": 201, "__body": json.dumps({"errCode": 0, "data": {"list": []}})})
        raise RuntimeError("main frame fetch failed")


async def main() -> int:
    d = await fetch_post_list(Page("ok"))
    check("① 主帧成功直接返回", (d.get("data") or {}).get("list") is not None,
          str((d.get("data") or {}).get("list"))[:40])

    d2 = await fetch_post_list(Page("fail", [Frame("https://channels.weixin.qq.com/micro/content/post/list", "ok")]))
    check("② 主帧失败退 iframe", (d2.get("data") or {}).get("list"), "回退成功")

    try:
        await fetch_post_list(Page("fail"))
        check("③ 全失败必须抛 ChannelsListError", False, "没抛异常")
    except ChannelsListError as exc:
        check("③ 全失败必须抛 ChannelsListError", True, str(exc)[:70])

    try:
        await fetch_post_list(Page("empty"))
        check("④ 空列表是合法结果（errCode=0）", True, "")
    except ChannelsListError as exc:
        check("④ 空列表是合法结果（errCode=0）", False, str(exc)[:60])

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
