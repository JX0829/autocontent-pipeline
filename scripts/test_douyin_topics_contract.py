#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音「话题真生效」契约测试（不需要浏览器）。

防的是什么：2026-11-16 用户反馈「抖音末尾话题还是纯文本」。实测纯文本 `#大模型`
在抖音只是文字；只有敲 `#关键词` 并从联想面板点选，编辑器才插入真话题节点：

    <span data-fake-text><div data-mention="#"><span ...>#大模型</span></div></span>

这里把「必须怎么做」钉成静态契约 + 一段可跑的逻辑测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
FORM = (PROJ / "src" / "providers" / "douyin_form.py").read_text(encoding="utf-8")
PROV = (PROJ / "src" / "providers" / "douyin.py").read_text(encoding="utf-8")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def main() -> int:
    print("=== 抖音话题契约 ===")
    check("① 正文不再拼话题（build_caption 第三参数传空）",
          'full_text = build_caption("", content_text.strip(), [])' in PROV)
    check("① 旧写法已移除（正文不再把 topics 拼进去）",
          'full_text = build_caption("", content_text.strip(), topics)' not in PROV)
    check("② 走联想面板：敲 #关键词 后点匹配项",
          '[class*=tag-hash-view-name]' in FORM and "delay=160" in FORM)
    check("③ 落地判据是真话题节点 [data-mention=\"#\"]",
          'data-mention=\\"#\\"' in FORM or "data-mention=\"#\"" in FORM)
    check("③ 每敲一个新话题前换行（面板才会刷新）",
          'page.keyboard.press("Enter")' in FORM)
    # ④⑤ 2026-11-18 真机后口径变了：重试改成「原词 → 短词回退」，且失败**不再**留纯文本
    #    （留纯文本正是 `据隐私` / `######` 残渣的来源）。
    check("④ 每个话题有重试 + 短词回退（原词无联想项时退到前两字）",
          "for attempt, cand in enumerate(variants, start=1):" in FORM
          and "variants = [tag] + ([tag[:2]] if len(tag) > 2 else [])" in FORM)
    check("⑤ 未落地如实记录且不留残渣（不虚报成功）",
          "未成为真话题（已回退，不留残渣）" in FORM and "topics_ok" in PROV)
    check("⑥ 结果带 topics_declared/topics_landed/topics_ok",
          all(k in PROV for k in ('"topics_declared"', '"topics_landed"', '"topics_ok"')))
    check("⑦ 正文校验用 ACE DOM 判据（innerText 无 \\n 不算失败）",
          "ace-line" in FORM and "DOM 校验" in FORM)
    check("⑧ 回读比对时先切掉话题段（话题是真节点，会多出 #词）",
          '.split("#")[0]' in PROV)
    check("⑨ 重试前清空描述框（否则正文会叠成两遍）",
          "Meta+A" in FORM and "_clear()" in FORM)

    sys.path.insert(0, str(PROJ))
    try:
        from src.providers.douyin_form import fill_topics  # noqa: F401
        check("⑩ fill_topics 可导入", callable(fill_topics))
    except Exception as exc:  # noqa: BLE001
        check("⑩ fill_topics 可导入", False, str(exc))

    print(f"\nDOUYIN TOPICS CONTRACT {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
