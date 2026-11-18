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
from pathlib import Path
from typing import Any

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
              // R4 真机实测：ACE 编辑器保留段落时每段一个 `.ace-line`，所以按「非空行数」
              // 判；wantLines 必须按 /\n+/ 数（旧代码用 /\n{2,}/ 且依赖失效选择器，
              // 于是恒为 lines:0 / wantLines:1 的假红，还触发无效重试）。
              const wantLines = String(exp).split(/\n+/).filter(x => x.trim()).length;
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

    # 真机（2026-11-18，6 个话题）实测三种写法结果一样：前 5 个都成真话题，
    # **最后一个必然失败**，且会在正文尾巴留下 `#数 据隐私` 这类半截残渣。
    # 原因不是「点不点联想」，而是打字过程中平台自己插了空格把词切断。
    # 因此判据变成：逐个话题**增量校验**，不成就不留残渣（回退刚才敲的字），
    # 不再「失败就按纯文本留着」——那正是残渣的来源。
    def _state() -> dict:
        try:
            return dict(page.evaluate(_TOPIC_STATE_JS))
        except Exception:  # noqa: BLE001
            return {"count": 0, "last": ""}

    landed_as: dict = {}
    dropped: list[str] = []
    for tag in want:
        got = False
        # 2026-11-18 定稿（方案A）：**没有联想项就直接丢弃这个话题**，优先保证简介干净。
        # 不做「退到前两字」的改名替换（`数据隐私` → `数据` 等于悄悄换了话题，审批时看不出来），
        # 也不留纯文本残渣。丢弃要记账，好让上层知道少了哪个。
        for attempt in (1, 2):
            before = _state()["count"]
            if attempt == 1:
                page.keyboard.insert_text(f"#{tag}")   # 整词插入：逐字敲会被平台切成「数 据隐私」
            else:
                page.keyboard.type(f"#{tag}", delay=160)
            clicked = ""
            for _ in range(16):                  # 最多等 ~4.8s 联想面板
                page.wait_for_timeout(300)
                clicked = page.evaluate(_CLICK_SUGGEST_JS, tag)
                if clicked:
                    break
            page.wait_for_timeout(500)
            st = _state()
            last_txt = str(st.get("last") or "").strip()
            # 平台话题名可能比声明更长（如 数据隐私 → 数据隐私保护），互相包含即算落地
            matched = bool(last_txt) and (last_txt == tag or tag in last_txt
                                           or last_txt in tag)
            if st["count"] > before and matched:
                landed.append(tag)
                landed_as[tag] = last_txt or tag
                got = True
                if (last_txt or tag) != tag:
                    log.warning(f"[douyin] 话题 #{tag} 无对应联想项，改用平台话题 #{landed_as[tag]}")
                else:
                    log.info(f"[douyin] 话题落地 #{tag}（联想项 {clicked or '?'}，第 {attempt} 次）")
                break
            # 没落地：用 DOM Range 精确删掉「最后一个真话题之后」的残渣
            _delete_tail(page)
            page.wait_for_timeout(300)
            log.warning(f"[douyin] 话题 #{tag} 第 {attempt} 次未落地"
                        f"（count {before}→{st['count']}，last={st.get('last')!r}），已回退重试")
        if not got:
            dropped.append(tag)
            problems.append(f"#{tag} 无联想项，已丢弃（不留残渣）")

    # R7：清尾部残渣（等于某 tag 后缀的游离文本）→ R8：终态自证
    tail = clean_topic_tail(page, want, log_obj=log)
    final = verify_topics(page, declared)
    if not final["ok"]:
        problems.extend(final["problems"])
    ok = len(landed) >= len(declared) and final["ok"]
    return {"declared": declared, "landed": landed, "landed_as": landed_as,
            "dropped": dropped, "ok": ok, "problems": problems,
            "final": final, "tail": tail}


_TOPIC_STATE_JS = r"""() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return {count: 0, last: ''};
  const ZW = /[\u200b\u200c\u200d\ufeff]/g;
  const ms = [...ed.querySelectorAll('[data-mention="#"]')];
  const last = ms.length ? ((ms[ms.length - 1].innerText || '').replace(ZW, '')
                           .trim().replace(/^#/, '')) : '';
  let tailLen = 0;
  if (ms.length) {
    const r = document.createRange();
    r.setStartAfter(ms[ms.length - 1]);
    r.setEnd(ed, ed.childNodes.length);
    const d = document.createElement('div');
    d.appendChild(r.cloneContents());
    tailLen = ((d.innerText || '').replace(ZW, '')).replace(/\s+$/g, '').length;
  }
  return {count: ms.length, last, tailLen};
}"""

_CLICK_SUGGEST_JS = r"""(kw) => {
  const names = [...document.querySelectorAll('[class*=tag-hash-view-name]')]
      .filter(e => e.offsetHeight);
  const norm = s => (s || '').trim().replace(/^#/, '');
  const pick = names.find(e => norm(e.innerText) === kw)
            || names.find(e => norm(e.innerText).startsWith(kw))
            || names.find(e => norm(e.innerText).includes(kw));
  if (!pick) return '';
  const item = (pick.closest('[class*=tag-hash]') && pick.closest('[class*=tag-hash]').parentElement)
            || pick.closest('[class*=tag-]') || pick;
  item.click();
  return (pick.innerText || '').trim();
}"""

COVER_ENTRY_TEXTS = ("选择封面", "设置封面", "编辑封面", "更换封面", "上传封面")
CONFIRM_TEXTS = ("确定", "完成", "保存", "下一步", "应用")


def _img_png(page, el, max_w: int = 512) -> bytes:
    """把页面里某个 <img> 的**原始像素**画到 canvas 再取回 PNG。

    为什么不用 element.screenshot()：抖音封面缩略图只有 61×81，截屏后 dHash 完全是噪声
    （实测距离恒 33~36，同图不同图分不开）。而这张缩略图的 `src` 是 blob，指向**满分辨率**
    的原图 —— 经 canvas 读回来再比，才是同一张图的真像素。
    """
    import base64

    data_url = el.evaluate(
        """(img, maxW) => {
            const nw = img.naturalWidth || 0, nh = img.naturalHeight || 0;
            if (!nw || !nh) return '';
            const W = Math.min(maxW, nw);
            const H = Math.max(1, Math.round(W * nh / nw));
            const c = document.createElement('canvas');
            c.width = W; c.height = H;
            c.getContext('2d').drawImage(img, 0, 0, W, H);
            try { return c.toDataURL('image/png'); } catch (e) { return ''; }
        }""", max_w)
    if not data_url or "," not in str(data_url):
        raise RuntimeError("canvas 取像素失败（可能是跨域图）")
    return base64.b64decode(str(data_url).split(",", 1)[1])


def set_cover(page, cover_path: str, *, log_obj=None) -> dict:
    """上传自定义封面并**回读像素比对**（R1 / R2 / R3）。

    为什么不能只「点一下上传」就算成功：封面入口是平台易变区，上传后也可能
    被平台自己的「智能封面」覆盖。唯一的硬证据是**回读预览图与我上传的那张
    图 dHash 一致**（同图 0~2，异图 >20，阈值 12）。

    返回 {"ok": bool, "entry":..., "dhash_dist":..., "error":...}；
    ok=False 时调用方必须 `COVER_UPLOAD_FAILED` 阻断发布（不许静默回落抽帧）。
    """
    log = log_obj or logger
    from src.utils.img_digest import same_image_any_crop

    path = Path(str(cover_path or ""))
    detail: dict = {"ok": False, "cover": str(path)}
    if not path.exists():
        detail["error"] = f"封面文件不存在：{path}"
        return detail

    # ① 找入口（文案多写法，逐个试；都没有就明确失败，不瞎点）
    entry = None
    for txt in COVER_ENTRY_TEXTS:
        loc = page.locator(f"text={txt}").first
        try:
            if loc.count() and loc.is_visible():
                entry = (txt, loc)
                break
        except Exception:  # noqa: BLE001
            continue
    if not entry:
        detail["error"] = f"找不到封面入口（试过 {COVER_ENTRY_TEXTS}）"
        return detail
    detail["entry"] = entry[0]
    try:
        entry[1].click()
        page.wait_for_timeout(1500)
    except Exception as exc:  # noqa: BLE001
        detail["error"] = f"点封面入口失败：{type(exc).__name__}: {exc}"
        return detail

    # ② 找图片类型的 file input（视频 input 的 accept 是 video/*，别喂错）
    target = None
    inputs = page.locator('input[type="file"]')
    for i in range(min(inputs.count(), 8)):
        el = inputs.nth(i)
        accept = (el.get_attribute("accept") or "").lower()
        if "image" in accept or "png" in accept or "jpg" in accept or "jpeg" in accept:
            target = el
            break
    if target is None:
        detail["error"] = "封面对话框里找不到图片上传框"
        return detail
    try:
        target.set_input_files(str(path))
        page.wait_for_timeout(3000)
    except Exception as exc:  # noqa: BLE001
        detail["error"] = f"上传封面失败：{type(exc).__name__}: {exc}"
        return detail

    # ③ 回读：挑「满分辨率的那张上传图」。真机事实（2026-11-18 DOM dump）：
    #    弹窗里面积最大的是手机预览卡（243×515，压着头像/点赞/音乐 UI）；
    #    真正代表封面的缩略图是 61×81 的 blob <img>，其 naturalWidth 仍是 1080。
    #    所以判据 = canvas 取缩略图背后的**原图像素** 与 cover.png 比（裁切感知）。
    cands: list[tuple[int, Any]] = []
    for sel in ('img[src^="blob:"]', 'img[src^="data:image"]'):
        loc = page.locator(sel)
        try:
            n = min(loc.count(), 12)
        except Exception:  # noqa: BLE001
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                nw = int(el.evaluate("(img) => img.naturalWidth || 0") or 0)
                nh = int(el.evaluate("(img) => img.naturalHeight || 0") or 0)
                cands.append((nw * nh, el))
            except Exception:  # noqa: BLE001
                continue
    if not cands:
        detail["error"] = "上传后找不到封面缩略图（blob/data img），无法自证"
        return detail
    cands.sort(key=lambda c: -c[0])
    detail["preview_natural_px"] = cands[0][0]
    best_el = cands[0][1]
    shot_path = Path("/tmp") / "douyin_cover_preview.png"
    try:
        png = _img_png(page, best_el)
        shot_path.write_bytes(png)
        detail["preview_png"] = str(shot_path)
        ok, dist, label = same_image_any_crop(str(path), png)
        detail["dhash_dist"] = dist
        detail["dhash_crop"] = label
        detail["ok"] = bool(ok)
        if not ok:
            detail["error"] = (f"缩略图背后的原图与 cover.png 不一致（最小 dHash 距离 {dist} "
                               f"@裁切{label} > 12）")
            return detail
    except Exception as exc:  # noqa: BLE001
        detail["error"] = f"封面像素回读失败：{type(exc).__name__}: {exc}"
        return detail

    # ④ 确认（有的弹窗要再点一次「确定/完成」才生效）
    for txt in CONFIRM_TEXTS:
        btn = page.locator(f'button:has-text("{txt}")').first
        try:
            if btn.count() and btn.is_visible():
                btn.click()
                page.wait_for_timeout(1500)
                detail["confirm"] = txt
                break
        except Exception:  # noqa: BLE001
            continue
    log.info(f"[douyin] 封面上传成功并自证（entry={detail.get('entry')} "
             f"dhash={detail.get('dhash_dist')} confirm={detail.get('confirm')}）")
    return detail


def topic_tail_residue(page) -> str:
    """最后一个真话题节点之后残留的纯文本（R7 的靶子）。

    2026-11-18 实测：`fill_topics` 敲 `#关键词` 点联想项后，mention 节点替换了 `#`，
    但敲进去的关键词没被完全吃掉，尾部留下 `据隐私`（`数据隐私` 少了首字）、
    `wen4`（`Qwen4` 少了首字符）这类残渣，用户在作品页肉眼可见。
    """
    try:
        return str(page.evaluate(
            """() => {
              const ed = document.querySelector('div[contenteditable="true"]');
              if (!ed) return '';
              const ms = [...ed.querySelectorAll('[data-mention="#"]')];
              const ZW = /[\u200b\u200c\u200d\ufeff]/g;
              if (!ms.length) return (ed.innerText || '').replace(ZW, '').trim();
              const r = document.createRange();
              r.setStartAfter(ms[ms.length - 1]);
              r.setEnd(ed, ed.childNodes.length);
              const box = document.createElement('div');
              box.appendChild(r.cloneContents());
              return (box.innerText || '').replace(ZW, '').replace(/\u00a0/g, ' ').trim();
            }"""))
    except Exception:  # noqa: BLE001 — 读不到就当没有残渣，最终还有终态校验兜底
        return ""


_DELETE_TAIL_JS = r"""() => {
  const ed = document.querySelector('div[contenteditable="true"]');
  if (!ed) return false;
  const ms = [...ed.querySelectorAll('[data-mention="#"]')];
  const r = document.createRange();
  if (ms.length) r.setStartAfter(ms[ms.length - 1]);
  else r.selectNodeContents(ed);
  r.setEnd(ed, ed.childNodes.length);
  if (r.collapsed) return false;
  ed.focus();
  const s = window.getSelection();
  s.removeAllRanges();
  s.addRange(r);
  document.execCommand('delete');
  return true;
}"""


def _delete_tail(page) -> bool:
    """用 DOM Range 精确删掉「最后一个真话题之后」的内容。

    为什么不用 Backspace：innerText 的字符数和 DOM 字符数不等（话题节点带包裹 span、
    平台自己插空格），按长度盲按会多吃掉一个话题 —— 真机表现为 6 个话题变 2 个。
    """
    try:
        return bool(page.evaluate(_DELETE_TAIL_JS))
    except Exception:  # noqa: BLE001
        return False


def clean_topic_tail(page, tags, log_obj=None) -> dict:
    """清掉话题尾部的游离文本（等于某 tag 后缀的才清，别误删用户正文）。"""
    log = log_obj or logger
    detail = {"before": "", "after": "", "removed": "", "cleaned": False}
    residue = topic_tail_residue(page)
    detail["before"] = residue
    if not residue:
        return detail
    plain = residue.replace(" ", "")
    hit = next((t for t in tags if plain and plain.endswith(t[len(t) // 2:])), "")
    if not hit:
        log.warning(f"[douyin] 话题尾部有游离文本但与任何话题无关，保留原样：{residue!r}")
        detail["after"] = residue
        return detail
    try:
        _delete_tail(page)
        page.wait_for_timeout(300)
        detail["cleaned"] = True
        detail["removed"] = residue
        detail["after"] = topic_tail_residue(page)
        log.info(f"[douyin] 已清掉话题尾部残渣 {residue!r}（属于 #{hit}）")
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[douyin] 清残渣失败：{type(exc).__name__}: {exc}")
        detail["after"] = residue
    return detail


def verify_topics(page, declared) -> dict:
    """R8 终态自证：真话题数 == 声明数，且尾部无游离文本。"""
    want = [str(t).strip().lstrip("#") for t in (declared or []) if str(t).strip()]
    got = count_topics(page)
    residue = topic_tail_residue(page)
    problems = []
    if got < len(want):
        problems.append(f"真话题节点 {got} 个，声明 {len(want)} 个")
    if residue:
        problems.append(f"尾部残留纯文本 {residue!r}")
    return {"ok": not problems, "count": got, "want": len(want),
            "residue": residue, "problems": problems}


def count_topics(page) -> int:
    """描述框里当前的真话题节点数（`[data-mention="#"]`）。"""
    try:
        return int(page.evaluate(
            "() => document.querySelectorAll('[data-mention=\"#\"]').length"))
    except Exception:  # noqa: BLE001
        return 0


def caption_dom(page) -> dict:
    """描述框的真实 DOM 事实（A2/A5 的证据来源）：非空 `.ace-line` 行数 + 文本长度。"""
    js = r"""() => {
      const ed = document.querySelector('div[contenteditable="true"]');
      if (!ed) return {lines: 0, wantLines: 0, flatLen: 0, reason: 'no_editor'};
      const ZW = /[\u200b\u200c\u200d\ufeff]/g;
      const lines = [...ed.querySelectorAll('.ace-line')]
          .map(e => (e.innerText || '').replace(ZW, '').trim()).filter(Boolean);
      const txt = (ed.innerText || '').replace(ZW, '');
      return {lines: lines.length,
              wantLines: txt.split(/\n+/).filter(x => x.trim()).length,
              flatLen: txt.replace(/\s+/g, '').length};
    }"""
    try:
        return dict(page.evaluate(js))
    except Exception:  # noqa: BLE001
        return {"lines": 0, "wantLines": 0, "flatLen": 0, "reason": "evaluate_failed"}


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
