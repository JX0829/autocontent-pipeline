#!/usr/bin/env python3
"""P0-1 / P0-2 离线回归（SPEC-20261119 R1~R6）。

P0-1 点「发布」被 semi-modal-wrap 遮挡 → 30s 超时：
  · 白名单弹层（章节要点/封面建议/画质提示）要先关掉，关不掉按 Esc；
  · 没有弹层时**零操作**（幂等，R2）；
  · 不认识的遮挡层不许乱点，要把 class + innerText 前 120 字带出来（R3）。

P0-2 成功凭证是时间戳不是作品 ID：
  · 代码里禁止再出现 `readback_<时间戳>`；
  · 作品 ID 只认 DOM `/video/<id>`、data 属性、创作者中心接口三种来源；
  · 拿不到 ID 时 `_verify_published` 返回 draft_id=None，调用方必须记 PUBLISHED_NO_ID（R6）。

跑法：python3 scripts/test_douyin_publish_p0.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from src.providers import douyin_form  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


# ── 假 Playwright 页面 ───────────────────────────────────────────────────────
class FakeLocator:
    """最小 locator：支持 count/is_visible/inner_text/get_attribute/click。

    click 会记到 page.clicks，并在点「关闭/知道了/取消」时把遮罩节点置为不可见
    ——真机上点掉弹层后遮罩就消失了，测试必须同样演进，否则永远判 blocked。
    """

    def __init__(self, page, nodes):
        self.page = page
        self.nodes = nodes

    @property
    def first(self):
        return FakeLocator(self.page, self.nodes[:1])

    def nth(self, i):
        return FakeLocator(self.page, self.nodes[i:i + 1])

    def count(self):
        return len(self.nodes)

    def is_visible(self):
        return bool(self.nodes) and self.nodes[0].get("visible", True)

    def inner_text(self):
        return self.nodes[0].get("text", "") if self.nodes else ""

    def get_attribute(self, name):
        return self.nodes[0].get("attrs", {}).get(name, "") if self.nodes else ""

    def click(self, timeout: int = 0):
        if not self.nodes:
            raise RuntimeError("no node")
        self.page.clicks.append(self.nodes[0].get("text", ""))
        if any(k in self.nodes[0].get("text", "") for k in ("关闭", "知道了", "取消")):
            self.page._remove_overlays()


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.keys.append(key)
        if key == "Escape":
            self.page._remove_overlays()


class FakePage:
    """极简页面：selectors 映射选择器 → 节点列表；记录点击与按键。"""

    def __init__(self, selectors=None, evaluate_result=None, api_items=None):
        self.selectors = selectors or {}
        self.evaluate_result = evaluate_result
        self.api_items = api_items or []
        self.clicks: list[str] = []
        self.keys: list[str] = []
        self.request = self
        self.keyboard = _FakeKeyboard(self)

    def _remove_overlays(self):
        for nodes in self.selectors.values():
            for n in nodes:
                cls = str(n.get("attrs", {}).get("class", ""))
                if "modal" in cls or "portal" in cls:
                    n["visible"] = False

    def locator(self, sel):
        return FakeLocator(self, self.selectors.get(sel, []))

    # 真机上 page.keyboard 是对象（不是方法），这里必须同样是属性


    def evaluate(self, js, arg=None):
        if callable(self.evaluate_result):
            return self.evaluate_result(js, arg)
        return self.evaluate_result

    def wait_for_timeout(self, ms):
        return None

    # request.get 替身
    def get(self, url, timeout=0):
        items = self.api_items

        class R:
            def __init__(self, payload):
                self._it = payload
                self.ok = bool(payload)

            def json(self):
                return {"aweme_list": self._it}

        return R(items)


def overlay_row(text: str, cls: str = "semi-modal-wrap") -> dict:
    return {"text": text, "attrs": {"class": cls}, "visible": True}


def main() -> int:
    print("── P0-1 遮挡弹层 ──")
    # 1) 无弹层 → 零操作
    page = FakePage({})
    r = douyin_form.dismiss_blockers(page)
    check("无弹层时零操作（幂等）", r["action"] == "none" and not page.clicks, f"{r} clicks={page.clicks}")

    # 2) 白名单弹层 + 有关闭按钮 → dismissed
    page = FakePage({
        'div[role="modal"], div[class*="semi-modal-wrap"]': [overlay_row("已智能生成章节要点，确认并添加")],
        'button:has-text("关闭")': [{"text": "关闭", "attrs": {}, "visible": True}],
    })
    r = douyin_form.dismiss_blockers(page)
    check("白名单弹层（章节要点）被关闭", r["action"] == "dismissed" and page.clicks, f"{r} clicks={page.clicks}")
    check("关闭动作打到「关闭」按钮", any("关闭" in c for c in page.clicks), str(page.clicks))

    # 3) 白名单弹层但无按钮 → Esc 兜底
    page = FakePage({'div[class*="semi-portal"]': [overlay_row("封面建议：选一张更清晰的封面吧", "semi-portal")]})
    r = douyin_form.dismiss_blockers(page)
    check("无关闭按钮时按 Esc 兜底", r["action"] in ("escape", "blocked") and r["closed_by"] == "Escape",
          f"{r}")

    # 4) 非白名单遮挡 → 不乱点，blocked + class/text
    page = FakePage({'div[role="modal"], div[class*="semi-modal-wrap"]':
                     [overlay_row("请完成安全验证后继续操作", "semi-modal-wrap")]})
    r = douyin_form.dismiss_blockers(page)
    check("不认识的遮挡层不乱点（blocked）", r["action"] == "blocked" and not page.clicks,
          f"{r} clicks={page.clicks}")
    check("blocked 时带出 class", "semi-modal-wrap" in r["class"], r["class"])
    check("blocked 时带出文案前 120 字", "安全验证" in r["text"], r["text"])

    # 5) describe_obstruction 截断
    long_text = "章节要点" + "很长的说明" * 60
    page = FakePage({'div[role="modal"], div[class*="semi-modal-wrap"]': [overlay_row(long_text)]})
    obs = douyin_form.describe_obstruction(page)
    check("describe_obstruction 返回 found", obs["found"])
    check("describe_obstruction 文案截断到 120", len(obs["text"]) <= 120, str(len(obs["text"])))

    print("── P0-2 真实作品 ID ──")
    src = "\n".join(l for l in (PROJ / "src/providers/douyin.py").read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    check("源码不再出现 readback_<时间戳>", not re.search(r'readback_\{int\(time\.time\(\)\)\}', src))
    check("有 POST_ID_UNAVAILABLE 分支", "POST_ID_UNAVAILABLE" in src)
    check("有 PUBLISHED_NO_ID 状态", "PUBLISHED_NO_ID" in src)

    from src.providers.douyin import DouyinProvider

    prov = DouyinProvider.__new__(DouyinProvider)  # 不跑 __init__（不连 CDP）

    # 6) DOM 命中
    page = FakePage(evaluate_result={"matched": {"id": "7412345678901234567", "text": "标题", "src": "href"},
                                     "candidates": [{"id": "7412345678901234567", "src": "href"}]})
    got = prov._extract_aweme_id(page, "某标题")
    check("DOM /video/<id> 命中", got["id"] == "7412345678901234567" and got["source"].startswith("dom:"), str(got))

    # 7) DOM 无 → 接口兜底
    page = FakePage(evaluate_result={"matched": None, "candidates": []},
                    api_items=[{"aweme_id": "7500000000000000001", "desc": "某标题 正文"}])
    got = prov._extract_aweme_id(page, "某标题")
    check("接口兜底拿到 aweme_id", got["id"] == "7500000000000000001" and got["source"].startswith("api:"), str(got))

    # 8) 都拿不到 → None（不得编造）
    page = FakePage(evaluate_result={"matched": None, "candidates": []}, api_items=[])
    got = prov._extract_aweme_id(page, "某标题")
    check("拿不到 ID 返回 None（不编造）", got["id"] is None and got["source"] == "unavailable", str(got))

    print(f"\nDOUYIN P0 {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
