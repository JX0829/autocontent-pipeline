#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音发布表单「填 + 自证」单一实现（provider 与 CLI 共用）。

2026-11-15 事故链（两条通道都中招）：
  · 标题被拼进简介：标题框 `作品标题` 从头空着 → 平台上「无作品描述 / 空标题」；
  · 简介用 `innerHTML=''` + `execCommand('insertText')` 直接改 DOM：React state 不更新，
    点发布时正文被整体丢弃 —— 而自检读的是 DOM，所以是**假绿**。
  · 17:57 那次（CLI 通道）作品数 44→45、回读 8 次全 miss，就是上面两条叠加。

本模块只做一件事：**点「发布」之前，必须能读回标题与正文**。
谁调用都要遵守 fail-closed：自证不过 → 不许点发布。
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

#: 抖音「作品标题」输入框的候选选择器（placeholder 会微调，多给几个兜底）
TITLE_SELECTORS = (
    'input[placeholder*="标题"]',
    'input[placeholder*="作品标题"]',
    'input[placeholder*="填写作品"]',
    'input[placeholder*="流量"]',
    'input[maxlength="30"]',
    'textarea[placeholder*="标题"]',
)

CAPTION_SELECTOR = 'div[contenteditable="true"]'


def _find_title_input(page):
    for sel in TITLE_SELECTORS:
        loc = page.locator(sel).first
        try:
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001 — 选择器不可用就试下一个
            continue
    return None


def title_value(page) -> str:
    """当前标题框的值（找不到框 / 读不到都返回空串）。"""
    loc = _find_title_input(page)
    if loc is None:
        return ""
    try:
        return (loc.input_value() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def fill_title(page, title: str, *, attempts: int = 3) -> tuple[bool, str, str]:
    """把标题填进「作品标题」框并回读自证。返回 (ok, landed, err)。"""
    want = (title or "").strip()[:30]
    if not want:
        return False, "", "标题为空"
    loc = _find_title_input(page)
    if loc is None:
        return False, "", "页面上找不到「作品标题」输入框"

    landed = ""
    for i in range(attempts):
        try:
            loc.click()
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Delete")
            loc.fill(want)          # fill 派发 input 事件，React 才认
            time.sleep(0.6)
            landed = (loc.input_value() or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"标题填写第 {i + 1} 轮异常: {exc}")
            landed = ""
        if landed and landed[:10] == want[:10]:
            logger.info(f"标题已落位（第 {i + 1} 轮）: {landed[:30]!r}")
            return True, landed, ""
        time.sleep(1)
    return False, landed, f"回读不一致 filled={landed[:40]!r}"


def fill_caption(page, text: str) -> bool:
    """正文写入：委托 `src/utils/douyin_caption.insert_caption`（唯一实现）。

    为什么不自己写：这里的旧实现只比对前 20 个字，抓不到「换行被压平 / 话题前后
    夹零宽字符与 NBSP」这类手机上看得见、前 20 字看不出的毛病（2026-11-15 用户
    截图就是这种）。现在统一走三段式构造 + 一次性写入 + 回读校验（换行数 / 零宽 /
    NBSP / 全文一致），不合格会重试并降级策略。
    """
    from src.utils.douyin_caption import describe, insert_caption

    body = (text or "").strip()
    if not body:
        return False
    editor = page.locator(CAPTION_SELECTOR).first
    if editor.count() == 0:
        ta = page.locator("textarea").first
        if ta.count() == 0:
            return False
        ta.click()
        ta.fill(body)
        return True
    def _dom_ok() -> tuple[bool, dict]:
        d = page.evaluate(
            r"""(exp) => {
              const ed = document.querySelector('div[contenteditable="true"]');
              if (!ed) return {ok: false, reason: 'no_editor'};
              const ZW = /[\u200b\u200c\u200d\ufeff]/g;
              const norm = s => String(s || '').replace(ZW, '').replace(/\u00a0/g, ' ').replace(/\s+/g, '');
              const flat = ed.innerText || '';
              const lines = [...ed.querySelectorAll('.ace-line')]
                  .map(e => (e.innerText || '').replace(ZW, '').trim()).filter(Boolean);
              const wantLines = String(exp).split(/\n{2,}/).filter(x => x.trim()).length;
              const want = norm(exp);
              const gotNorm = norm(flat);
              return {ok: gotNorm === want && lines.length >= wantLines,
                      textOk: gotNorm === want, lines: lines.length, wantLines,
                      gotLen: gotNorm.length, wantLen: want.length};
            }""",
            body,
        )
        return bool(d.get("ok")), d

    def _clear() -> None:
        """清空描述框（重试前必须清，否则正文会叠加成两遍）。"""
        try:
            editor.click()
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001
            pass

    res = insert_caption(page, editor, body, logger=logger)
    ok, dom = _dom_ok()
    if not ok:
        # insert_caption 的「换行数 ≥2」在 ACE 编辑器里是假红（段落是 .ace-line div，
        # innerText 不含 \n），所以以 DOM 判据为准；真不合格才清空重试一次。
        logger.warning("正文 DOM 校验不合格，清空重试一次：%s", dom)
        _clear()
        res = insert_caption(page, editor, body, logger=logger)
        ok, dom = _dom_ok()
    logger.info("正文写入 策略=%s 校验=%s", res["strategy"], dom)
    return ok


def fill_topics(page, topics, log_obj=None) -> dict:
    """把 `#话题` 变成抖音的**真话题节点**（不是纯文本）。

    2026-11-16 实测：纯文本 `#大模型` 发布后在抖音只是文字；只有在描述框里
    敲 `#关键词` 并从联想面板点选，编辑器才会插入真话题节点：

        <span data-mention="#"><span style="background: rgba(1,118,247,.12)">#大模型</span></span>

    所以这里逐个话题：敲 `#关键词` → 等联想项 → 点匹配项 → 数 `[data-mention="#"]` 增量。
    没有匹配项时如实记录（不假装成功），并补一个空格让后续话题不粘连。

    返回 {"declared": [...], "landed": [...], "ok": bool, "problems": [...]}
    """
    log = log_obj or logger
    want = [str(t).strip().lstrip("#") for t in (topics or []) if str(t).strip()]
    declared, landed, problems = list(want), [], []
    if not want:
        return {"declared": [], "landed": [], "ok": True, "problems": []}

    # 光标移到描述末尾，避免话题插进正文中间
    moved = page.evaluate(
        """() => {
          const ed = document.querySelector('div[contenteditable="true"]');
          if (!ed) return false;
          ed.focus();
          const r = document.createRange();
          r.selectNodeContents(ed); r.collapse(false);
          const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
          return true;
        }"""
    )
    if not moved:
        return {"declared": declared, "landed": [], "ok": False, "problems": ["描述编辑器不存在"]}

    def _count() -> int:
        try:
            return int(page.evaluate(
                "() => document.querySelectorAll('[data-mention=\"#\"]').length"))
        except Exception:  # noqa: BLE001 — 数不到就当归零
            return 0

    # 抖音的联想面板只在「新行里重新打 #」时稳定弹出（同一行接着打，面板可能不刷新）
    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
    except Exception:  # noqa: BLE001 — 按键失败就按原位置继续
        pass

    for tag in want:
        got = False
        for attempt in (1, 2):                   # 每个话题最多试两次（面板偶尔不刷新）
            before = _count()
            page.keyboard.type(f"#{tag}", delay=160)
            clicked = ""
            for _ in range(16):                  # 最多等 ~4.8s 联想面板
                page.wait_for_timeout(300)
                clicked = page.evaluate(
                    """(kw) => {
                      const names = [...document.querySelectorAll('[class*=tag-hash-view-name]')]
                          .filter(e => e.offsetHeight);
                      const pick = names.find(e => (e.innerText || '').trim() === kw)
                                || names.find(e => (e.innerText || '').trim().startsWith(kw));
                      if (!pick) return '';
                      const item = pick.closest('[class*=tag-hash]').parentElement
                                || pick.closest('[class*=tag-]');
                      (item || pick).click();
                      return (pick.innerText || '').trim();
                    }""",
                    tag,
                )
                if clicked:
                    break
            page.wait_for_timeout(500)
            if _count() > before:
                landed.append(tag)
                got = True
                log.info(f"[douyin] 话题落地 #{tag}（联想项 {clicked or '?'}，第 {attempt} 次）")
                break
            # 没落地：把这几个字符退掉，重试
            for _ in range(len(tag) + 1):
                page.keyboard.press("Backspace")
            page.wait_for_timeout(400)
        if not got:
            problems.append(f"#{tag} 无匹配联想项，已按纯文本留下")
            page.keyboard.type(f"#{tag} ", delay=60)
            log.warning(f"[douyin] 话题 #{tag} 未成为真话题（两次都没匹配）")

    ok = len(landed) >= len(declared)
    return {"declared": declared, "landed": landed, "ok": ok, "problems": problems}


def count_topics(page) -> int:
    """描述框里当前的真话题节点数（`[data-mention="#"]`）。"""
    try:
        return int(page.evaluate(
            "() => document.querySelectorAll('[data-mention=\"#\"]').length"))
    except Exception:  # noqa: BLE001
        return 0


def caption_text(page) -> str:
    editor = page.locator(CAPTION_SELECTOR).first
    try:
        if editor.count() == 0:
            return ""
        return (editor.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def assert_form_ready(page, title: str, caption: str) -> tuple[bool, dict]:
    """**点发布前最后一道**：读回标题与正文，任一不达标就 fail-closed。

    放在话题、AIGC 勾选、可见性选择**之后**调用 —— 这些操作会触发 React 重渲染，
    早填好的标题可能被清掉，所以必须在最后一刻验。
    """
    want_title = (title or "").strip()[:10]
    want_cap = (caption or "").strip()[:20]

    got_title = title_value(page)
    got_cap = caption_text(page)
    detail = {
        "title_want": (title or "")[:30],
        "title_got": got_title[:30],
        "caption_want": (caption or "")[:20],
        "caption_got": got_cap[:40],
        "title_ok": bool(want_title) and got_title[:10] == want_title,
        "caption_ok": bool(want_cap) and want_cap in got_cap,
    }
    ok = detail["title_ok"] and detail["caption_ok"]

    if not ok:
        # 先尝试就地补填再验一次（一次机会，避免把偶发重渲染当成永久失败）
        if not detail["title_ok"]:
            ok_t, landed, _ = fill_title(page, title)
            detail["title_got"] = landed[:30]
            detail["title_ok"] = ok_t
        if not detail["title_ok"] and want_title:
            logger.error(f"SUBMIT_BLOCKED: 标题读回为空/不符 {detail}")
            return False, detail
        if not detail["caption_ok"]:
            fill_caption(page, caption)
            detail["caption_got"] = caption_text(page)[:40]
            detail["caption_ok"] = want_cap in detail["caption_got"]
        ok = detail["title_ok"] and detail["caption_ok"]

    if not ok:
        logger.error(f"SUBMIT_BLOCKED: 表单自证不通过 {detail}")
    else:
        logger.info(f"表单自证通过: title={detail['title_got']!r} caption_len={len(got_cap)}")
    return ok, detail
