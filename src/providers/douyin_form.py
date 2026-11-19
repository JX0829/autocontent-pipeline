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
              const ZW = [0x200b, 0x200c, 0x200d, 0xfeff];
              const stripZW = x => [...String(x || '')].filter(c => !ZW.includes(c.codePointAt(0))).join('');
              const isSpace = c => [0x20, 0x0a, 0x0d, 0x09, 0x00a0, 0x3000].includes(c.codePointAt(0));
              const norm = s => [...stripZW(s)].filter(c => !isSpace(c)).join('');
              const flat = ed.innerText || '';
              // 2026-11-18 事故：`.ace-line` 在现版编辑器里 0 命中 → lines 恒 0 → 判据恒红。
              // 现在按「块级子节点数」判分段（div/p/li 各算一段，退化用 innerText 换行数），
              // wantLines 按任意换行切（String.fromCharCode(10)），与写入侧 split_paragraphs 同一口径。
              const blocks = [...ed.children].filter(e => {
                const t = stripZW(e.innerText || '').trim();
                return t.length > 0 || e.querySelector('br,img,[data-mention]');
              }).length;
              const NL = String.fromCharCode(10);
              const innerLines = flat.split(NL).filter(x => x.trim()).length;
              const lines = Math.max(blocks, blocks === 0 ? innerLines : blocks);
              const wantLines = String(exp).split(NL).filter(x => x.trim()).length;
              const want = norm(exp);
              const gotNorm = norm(flat);
              return {ok: gotNorm === want && lines >= wantLines,
                      textOk: gotNorm === want, lines, blocks, innerLines, wantLines,
                      linesSource: blocks ? 'block_children' : 'inner_text',
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
        """话题芯片数（容忍多种结构：data-mention / span.topic / data-type=topic）。"""
        try:
            return int(page.evaluate(
                """() => {
                  const sel = '[data-mention], span.topic, span[data-type="topic"]';
                  const ed = document.querySelector('div[contenteditable="true"]');
                  return (ed || document).querySelectorAll(sel).length;
                }"""))
        except Exception:  # noqa: BLE001 — 数不到就当归零
            return 0

    # 抖音的联想面板只在「新行里重新打 #」时稳定弹出（同一行接着打，面板可能不刷新）
    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(250)
    except Exception:  # noqa: BLE001 — 按键失败就按原位置继续
        pass

    def _caret_to_end() -> None:
        """把光标放到编辑器内容真正末尾（芯片之后）—— 否则接着打字会覆盖前一个话题。"""
        try:
            page.evaluate(
                """() => {
                  const ed = document.querySelector('div[contenteditable="true"]');
                  if (!ed) return false;
                  ed.focus();
                  const r = document.createRange();
                  r.selectNodeContents(ed);
                  r.collapse(false);
                  const sel = window.getSelection();
                  sel.removeAllRanges();
                  sel.addRange(r);
                  return true;
                }""")
            page.wait_for_timeout(120)
        except Exception:  # noqa: BLE001 — 定位失败就按原位置继续
            pass

    def _drop_typed(tag: str) -> bool:
        """只删掉「刚才敲进去的 #tag 纯文本」，绝不盲按 Backspace（会吃掉真话题芯片）。"""
        try:
            picked = page.evaluate(
                """(kw) => {
                  const ed = document.querySelector('div[contenteditable="true"]');
                  if (!ed) return false;
                  const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                  let node = null, n;
                  while ((n = walker.nextNode())) {
                    const p = n.parentElement;
                    if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                    const t = (n.textContent || '').trim();
                    if (t && (t === kw || t.endsWith(kw))) node = n;
                  }
                  if (!node) return false;
                  const txt = node.textContent || '';
                  const idx = txt.lastIndexOf(kw);
                  if (idx < 0) return false;
                  const r = document.createRange();
                  r.setStart(node, idx);
                  r.setEnd(node, idx + kw.length);
                  const sel = window.getSelection();
                  sel.removeAllRanges();
                  sel.addRange(r);
                  return true;
                }""",
                f"#{tag}",
            )
            if not picked:
                return False
            page.keyboard.press("Backspace")
            page.wait_for_timeout(220)
            return True
        except Exception:  # noqa: BLE001
            return False

    for tag in want:
        got = False
        for attempt in (1, 2):                   # 每个话题最多试两次（面板偶尔不刷新）
            before = _count()
            _caret_to_end()
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
            _now = _count()
            if _now > before:
                landed.append(tag)
                got = True
                log.info(f"[douyin] 话题落地 #{tag}（联想项 {clicked or '?'}，第 {attempt} 次）")
                break
            # 没落地：只退掉刚敲的 #tag 纯文本再重试（不再盲按 Backspace）
            if not _drop_typed(tag):
                log.warning(f"[douyin] #{tag} 未落地且无法安全回退，停止重试")
                break
            page.wait_for_timeout(300)
        if not got:
            # 2026-11-18 18:50 真机（作品 57《英伟达圣诞季送Steam现金》）：残留「#显卡 卡卡」
            # 的正是在这条「联想列表无匹配」路径上 —— 敲进去的 #tag 是纯文本，平台的联想
            # 插入逻辑只吃掉一部分，留下 `卡卡` 这种尾巴，而按 chip 设计的清理判据摸不到。
            # 处置：**无匹配就不写纯文本**（纯文本话题本来也不生效，只会制造残渣）。
            problems.append(f"#{tag} 无匹配联想项，已跳过（不写纯文本，避免尾部残渣）")
            log.warning(f"[douyin] 话题 #{tag} 无匹配联想项，跳过不写（防尾部残渣）")

    # ── R7：清尾部游离文本（联想项只吃掉 `#`+首字符，余下如「据隐私」「wen4」残留）──
    repaired = _repair_tail(page, want, log)

    # ── R8：终态校验 —— 真话题数 == 声明数，且尾部无游离文本 ──
    final_count = _count()
    tail = _editor_tail(page)
    stray = _stray_tail(tail, want)
    # 真话题数不足：平台联想面板没给出该词（如 #Qwen4 无匹配）时属平台能力边界，
    # 记 problem 但不阻断发布；**尾部游离文本才是必须拦的**（用户截图里的 `据隐私`/`wen4`）。
    if final_count < len(declared):
        problems.append(f"真话题节点 {final_count} != 声明 {len(declared)}（按纯文本保留）")
    if stray:
        problems.append(f"描述尾部残留游离文本 {stray!r}")
    ok = not stray
    if not ok:
        log.error(f"[douyin] 话题终态不合格：count={final_count}/{len(declared)} tail={tail!r} stray={stray!r}")
    return {"declared": declared, "landed": landed, "ok": ok, "problems": problems,
            "count": final_count, "tail": tail, "tail_repaired": repaired, "stray": stray}


def _editor_tail(page, n: int = 24) -> str:
    """描述编辑器尾部 n 个字符（**只取纯文本，跳过话题芯片**）。

    2026-11-18 真机教训：早先直接 slice innerText，会把这几个话题芯片的文字
    （`#AI安全`）也算进尾部，于是把正常话题误判成残渣（实测误删 3 个真话题）。
    """
    try:
        return str(page.evaluate(
            """(n) => {
              const ed = document.querySelector('div[contenteditable="true"]');
              if (!ed) return '';
              const ZW = [0x200b, 0x200c, 0x200d, 0xfeff];
              const strip = x => [...String(x || '')]
                  .filter(c => !ZW.includes(c.codePointAt(0))).join('');
              const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
              let out = '', node;
              while ((node = walker.nextNode())) {
                const p = node.parentElement;
                if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                out += strip(node.textContent || '');
              }
              return out.slice(-n);
            }""", n) or "")
    except Exception:  # noqa: BLE001
        return ""


def _stray_tail(tail: str, tags) -> str:
    """尾部游离文本判定：末尾（去掉空格）等于某 tag 的后缀（如 数据隐私→据隐私、Qwen4→wen4）。"""
    t = (tail or "").rstrip()
    if not t:
        return ""
    for tag in tags:
        for cut in (1, 2):
            suf = str(tag)[cut:]
            if len(suf) >= 3 and t.endswith(suf):
                return suf
        if t.endswith(str(tag)):          # 整个 tag 都在（含 # 被吃掉的情况）
            return str(tag)
    return ""


def _repair_tail(page, tags, log) -> list:
    """清掉话题尾部游离文本（不改 DOM，用 Range 选中 + 原生 Backspace）。

    2026-11-18 真机教训：早先版本「End + 连续 Backspace」在焦点落在正文而非末尾时
    会把整段正文/话题芯片一起删掉（实测 count 6→0、正文只剩 \u200b）。现在：
      1. 先用 JS 精确选中「尾部游离文本节点」（range.createContextualRange 到该文本）；
      2. 按一次 Backspace（原生事件 → React 状态同步）；
      3. 每次前后比 `[data-mention="#"]` 数量，**一旦减少立即中止**，并把这个情况报上去。
    最多修 3 轮，每轮只删一个游离片段。
    """
    fixed = []
    for _ in range(3):
        tail = _editor_tail(page)
        stray = _stray_tail(tail, tags)
        if not stray:
            break
        before = _count_mentions(page)
        selected = page.evaluate(
            r"""(stray) => {
              const ed = document.querySelector('div[contenteditable="true"]');
              if (!ed) return false;
              const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
              let node = null, n;
              while ((n = walker.nextNode())) {
                const ZW = [0x200b, 0x200c, 0x200d, 0xfeff];
                const t = [...String(n.textContent || '')]
                    .filter(c => !ZW.includes(c.codePointAt(0))).join('').trim();
                if (t && t.endsWith(stray)) node = n;
              }
              if (!node) return false;
              const txt = node.textContent || '';
              const idx = txt.lastIndexOf(stray);
              const r = document.createRange();
              r.setStart(node, Math.max(0, idx));
              r.setEnd(node, Math.min(txt.length, idx + stray.length));
              const sel = window.getSelection();
              sel.removeAllRanges();
              sel.addRange(r);
              ed.focus();
              return true;
            }""",
            stray,
        )
        if not selected:
            log.warning(f"[douyin] 尾部游离文本 {stray!r} 无法定位（不是纯文本节点），放弃自动清理")
            break
        try:
            page.keyboard.press("Backspace")   # 删掉选中的游离片段（一次，不连按）
            page.wait_for_timeout(240)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[douyin] 清理尾部残渣失败：{type(exc).__name__}: {exc}")
            break
        after = _count_mentions(page)
        if after < before:
            log.error(f"[douyin] 清理残渣误伤话题节点（{before}→{after}），中止清理")
            fixed.append(f"{stray}(DAMAGED)")
            break
        fixed.append(stray)
        log.warning(f"[douyin] 清掉话题尾部游离文本 {stray!r}（话题节点 {before}→{after}）")
    return fixed


def _count_mentions(page) -> int:
    try:
        return int(page.evaluate(
            """() => {
                  const sel = '[data-mention], span.topic, span[data-type="topic"]';
                  const ed = document.querySelector('div[contenteditable="true"]');
                  return (ed || document).querySelectorAll(sel).length;
                }"""))
    except Exception:  # noqa: BLE001
        return 0


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


# ── 封面（SPEC-20261118 R1~R3）───────────────────────────────────────────────
COVER_ENTRY_TEXTS = ("选择封面", "编辑封面", "更换封面", "设置封面", "上传封面")
COVER_DONE_TEXTS = ("确定", "完成", "确认", "保存")


def _dh_window(png_path) -> list:
    """像素指纹：缩到 9x8 灰度后取横向差分（dHash），用来比「预览图 vs cover.png」。"""
    from PIL import Image
    with Image.open(png_path) as im:
        g = im.convert("L").resize((9, 8), Image.LANCZOS)
        px = list(g.getdata())
    bits = []
    for y in range(8):
        row = px[y * 9:(y + 1) * 9]
        bits.extend(1 if row[x] > row[x + 1] else 0 for x in range(8))
    return bits


def _dh_dist(a, b) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def set_cover(page, cover_path: str, *, log_obj=None, verify: bool = True) -> dict:
    """上传自定义封面并回读校验（SPEC-20261118 R1~R3）。

    返回 {"ok": bool, "code": str, "detail": str, "dist": int|None}
    · 找不到入口 / 上传超时 / 回读不通过 → ok=False，code=COVER_UPLOAD_FAILED
    上层必须据此阻断发布（不许静默回落系统抽帧）。
    """
    import os
    import tempfile

    log = log_obj or logger
    path = str(cover_path or "")
    if not path or not os.path.exists(path):
        return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": f"封面文件不存在: {path!r}"}

    opened = page.evaluate(
        """(texts) => {
          const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
          const cands = [...document.querySelectorAll('div,span,button,label,a,p')];
          for (const t of texts) {
            const el = cands.find(e => vis(e) && (e.innerText || '').trim() === t);
            if (el) { el.click(); return t; }
          }
          // 退一步：包含关键字且足够短的节点（如「选择封面」按钮里带图标）
          for (const t of texts) {
            const el = cands.find(e => vis(e) && (e.innerText || '').includes(t)
                                       && (e.innerText || '').trim().length <= t.length + 6);
            if (el) { el.click(); return t; }
          }
          return '';
        }""",
        list(COVER_ENTRY_TEXTS),
    )
    if not opened:
        return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                "detail": "页面找不到封面入口（选择封面/编辑封面）"}

    log.info(f"[douyin] 已点击封面入口：{opened}")
    page.wait_for_timeout(1500)

    # 上传：优先弹窗里的图片 input
    try:
        inputs = page.locator('input[type="file"]')
        idx = None
        for i in range(inputs.count()):
            acc = (inputs.nth(i).get_attribute("accept") or "")
            if "image" in acc or acc == "":
                idx = i
                break
        if idx is None:
            return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": "封面弹窗里没有图片上传 input"}
        inputs.nth(idx).set_input_files(os.path.abspath(path))
        log.info(f"[douyin] 封面上传已注入：{path}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                "detail": f"封面 set_input_files 失败: {type(exc).__name__}: {exc}"}

    # 等预览出现
    preview = None
    for _ in range(40):
        page.wait_for_timeout(500)
        preview = page.evaluate(
            """() => {
              const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
              const imgs = [...document.querySelectorAll('img')].filter(i => vis(i)
                  && i.naturalWidth > 60 && /blob:|douyinpic|byteimg/.test(i.currentSrc || i.src || ''));
              if (!imgs.length) return null;
              const i = imgs[imgs.length - 1];
              const r = i.getBoundingClientRect();
              return {src: (i.currentSrc || i.src || '').slice(0, 80), x: r.x, y: r.y, w: r.width, h: r.height};
            }"""
        )
        if preview:
            break
    if not preview:
        return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": "上传后 20s 内没出现封面预览"}

    # 点确定/完成
    done = page.evaluate(
        """(texts) => {
          const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
          const btns = [...document.querySelectorAll('button,div[role=button],span')].filter(vis);
          for (const t of texts) {
            const b = btns.find(e => (e.innerText || '').trim() === t);
            if (b) { b.click(); return t; }
          }
          return '';
        }""",
        list(COVER_DONE_TEXTS),
    )
    page.wait_for_timeout(1800)
    log.info(f"[douyin] 封面确认按钮：{done or '（未找到，视为弹窗自动关闭）'}")

    if not verify:
        return {"ok": True, "code": "", "detail": "已上传（未做回读）", "dist": None}

    # 回读校验：截当前封面缩略图区域 → dHash 与 cover.png 比
    dist = None
    try:
        shot = os.path.join(tempfile.mkdtemp(), "cover_preview.png")
        page.screenshot(path=shot, clip={"x": preview["x"], "y": preview["y"],
                                         "width": max(8, preview["w"]), "height": max(8, preview["h"])})
        dist = _dh_dist(_dh_window(shot), _dh_window(path))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[douyin] 封面回读截图失败：{type(exc).__name__}: {exc}")
        return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                "detail": f"封面回读失败（无法截图比对）: {exc}"}

    ok = dist is not None and dist <= 14
    if not ok:
        return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                "detail": f"封面回读不一致（dHash 距离 {dist} > 14）", "dist": dist}
    log.info(f"[douyin] 封面已上传并回读通过（dHash 距离 {dist}）")
    return {"ok": True, "code": "", "detail": f"dHash 距离 {dist}", "dist": dist}
