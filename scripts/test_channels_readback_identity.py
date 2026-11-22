#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号回读假阴性回归（2026-11-22）。

事故：片子 11:12 已发布（export/UzFfBgAAxJKnfEp2N…，post_list 首条可查），
系统判 failed、回执 2/3。根因是拿**期望标题**匹配，而平台把标题改写成正文首句
「特斯拉的FSD又要跨一个大版本了」。
"""
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.providers.channels_cdp import match_published_item  # noqa: E402

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


# 真实数据（11:12 那条）：标题被平台改写，正文是 LLM 版 video_desc
CT = 1795317152
OURS = "export/UzFfBgAAxJKnfEp2N33lkszT4DCaTrM60CHDgSqoFjgVQSnBXw"
ITEMS = [
    {"objectId": OURS, "createTime": CT,
     "desc": {"title": None, "description": "特斯拉的FSD又要跨一个大版本了。AI负责人说v15会更早预判危险、反应更快"}},
    {"objectId": "export/OLD0001", "createTime": CT - 86400,
     "desc": {"title": None, "description": "微软把2.3B模型塞进Windows"}},
    {"objectId": "export/OLD0002", "createTime": CT - 172800,
     "desc": {"title": None, "description": "英伟达动作模型跑到15000帧"}},
]
TITLE = "FSD V15模型扩到百亿参数"
BODY = "特斯拉的FSD又要跨一个大版本了"


def main() -> int:
    # ① 旧判据必然假阴性
    old_key = "".join(ch for ch in TITLE if ch.isalnum())[:8]
    old_hit = any(old_key in str((it.get("desc") or {}).get("description") or "") for it in ITEMS)
    check("① 复现假阴性：标题前 8 字确实匹配不到", not old_hit, old_key)

    # ② 新判据 A：新 objectId + 时间窗
    before = {"export/OLD0001", "export/OLD0002"}
    hit = match_published_item(ITEMS, before, CT + 3, BODY, TITLE)
    check("② 新 objectId 判据命中", bool(hit) and hit["match"] == "new_object_id", str(hit and hit["match"]))
    check("② 命中的就是那条真作品", bool(hit) and hit["objectId"].startswith("export/UzFfBgAAxJKnfEp2N"),
          str(hit and hit["objectId"])[:40])

    # ③ 快照缺失时退化到正文前缀
    hit2 = match_published_item(ITEMS, set(), CT + 3, BODY, TITLE)
    check("③ 无快照 → 正文前缀命中", bool(hit2) and hit2["match"] == "body_prefix", str(hit2 and hit2["match"]))

    # ④ 负例：真的没发出去（无新 id、正文不符）→ 必须返回 None
    hit3 = match_published_item(ITEMS, {OURS, "export/OLD0001", "export/OLD0002"}, CT + 3,
                                "这篇正文完全不同根本不匹配的文案内容", "毫不相关的标题")
    check("④ 没发出去时不误判成功", hit3 is None, str(hit3))

    # ⑤ 时间窗外的新 id 不算（防止把别人的作品认成自己的）
    hit4 = match_published_item(ITEMS, {"export/OLD0001", "export/OLD0002"}, CT + 3600,
                                "完全不匹配的正文", "无关标题")
    check("⑤ 时间窗外的作品不认", hit4 is None, str(hit4))

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
