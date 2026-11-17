#!/usr/bin/env python3
"""creator_take 门禁契约测试（正例 + 负例，全部真跑）。

覆盖：
  正例 · 四种扩展手法（类比/量化/猜测/抛问）应全部通过
  负例 · 缺字段 / 太短 / 抄原文 / 空话 / 绝对化 / 引流

用法：$PY scripts/test_creator_take_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.utils.creator_take_guard import check_creator_takes, overlap_ratio, summarize  # noqa: E402

PASS = FAIL = 0


def case(name: str, scenes: list, fact: str, expect_ok: bool) -> None:
    global PASS, FAIL
    errs = check_creator_takes(scenes, fact)
    ok = not errs
    good = ok == expect_ok
    print(f"  {'✅' if good else '❌'} {name}: {'通过' if ok else f'{len(errs)} 处不通过'}")
    if not good:
        for e in errs:
            print(f"       {e}")
    if good:
        PASS += 1
    else:
        FAIL += 1


FACT = ("DeepSeek 对长对话做出三项调整：单次输入压到约四万字，七天不活跃的对话不再保留，"
        "每日额度受限并提示算力不足。官方未发独立公告，11 月 14 日已有人在前端代码中看到额度提示逻辑。")


def scene(take, narration="DeepSeek 把长对话的三条口子同时收了。"):
    return [{"scene_index": 0, "segment_narration": narration, "creator_take": take}]


print("=== 正例：四种扩展手法 ===")
case("量化换算", scene("四万字大概是一份合同塞得下、一份财报塞不下的量，这个位置卡得挺巧。"), FACT, True)
case("类比同类", scene("同类产品里谁最能让你天天白用？评论区可以点个名，我挺好奇大家主力用哪个。"), FACT, True)
case("猜测动因", scene("如果是算力顶不住，接下来价格可能还得动；如果是给新模型腾地方，那说明有东西要发。"), FACT, True)
case("抛出问题", scene("云端额度该按天算还是按量算，你觉得哪种更经得起重度使用？"), FACT, True)

print("\n=== 负例：应该被拦下 ===")
case("缺 creator_take", scene(""), FACT, False)
case("太短（口号）", scene("这事挺重要"), FACT, False)
case("照抄原文", scene("单次输入压到约四万字，七天不活跃的对话不再保留，每日额度受限并提示算力不足。"), FACT, False)
case("空话", scene("这件事值得关注，影响深远，我们拭目以待行业后续变化。"), FACT, False)
case("绝对化", scene("这绝对是史上最好的额度策略，没有之一，用户只能接受。"), FACT, False)
case("引流", scene("细节我都整理好了，加V私信我，主页置顶有完整资料。"), FACT, False)

print("\n=== 重合率实测 ===")
t = "四万字大概是一份合同塞得下、一份财报塞不下的量，这个位置卡得挺巧。"
print(f"  量化换算 例 → 与原文重合 {overlap_ratio(t, FACT):.1%}")
t2 = "单次输入压到约四万字，七天不活跃的对话不再保留，每日额度受限。"
print(f"  照抄原文 例 → 与原文重合 {overlap_ratio(t2, FACT):.1%}")
print(f"  统计: {summarize(scene(t), FACT)}")

print(f"\n结论: {'PASS' if FAIL == 0 else 'FAIL'}（{PASS} 通过 / {FAIL} 失败）")
raise SystemExit(0 if FAIL == 0 else 1)
