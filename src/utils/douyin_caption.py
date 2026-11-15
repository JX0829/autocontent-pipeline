"""抖音「作品描述」排版：构造 / 一次插入 / 回读校验（唯一实现）。

背景（2026-10 实测）：旧写法 `editor.type(caption, delay=10)` 是**逐字符**输入，
① contenteditable 把 `\n` 吞成空格，三段被压成一行；
② 每敲一个 `#` 都会唤起话题联想，编辑器在话题前后插入 `\u200b` + `\xa0`。
平台读回的真实文本：换行 0、零宽 5、NBSP 8 —— 手机上看起来像乱码。

修法：
- 排版改三段式（标题 / 简介 / 话题），话题放最后，符合抖音阅读习惯；
- 一次性写入，不逐字符：JS 按段落插 `<div>` → `keyboard.insert_text` → `fill` 逐级降级；
- 写完**回读编辑页文本**做校验（换行数 / 零宽 / NBSP / 正文一致），
  不合格自动重试一次，仍不合格就返回问题清单，由调用方记录并告警（不再静默）。

本模块被 `scripts/publish_douyin_cdp.py`、`src/providers/douyin.py`、
`src/utils/video_desc.py` 共用 —— 三处口径必须一致，别再各写一套。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, List, Optional, Sequence

INVISIBLE = "\u200b\u200c\u200d\ufeff"   # 零宽字符（话题芯片插入的产物）
NBSP = "\xa0"
MIN_NEWLINES = 2                          # 标题 / 简介 / 话题 三段 = 至少 2 个换行


def _norm_tag(t: Any) -> str:
    s = str(t or "").strip()
    return s if s.startswith("#") else f"#{s}"


def build_caption(title: str, body: str, tags: Optional[Sequence[Any]] = None) -> str:
    """三段式：`标题` / `简介` / `#话题1 #话题2 …`，空段自动省略。

    旧格式是 `标题 #话题\\n正文`（话题夹在标题后），它正是触发编辑器逐字联想的入口，
    且平台读回时把整段压平，所以话题统一挪到末尾。
    """
    tag_str = " ".join(_norm_tag(t) for t in (tags or []) if str(t or "").strip())
    parts = [str(title or "").strip(), str(body or "").strip(), tag_str]
    return "\n\n".join(p for p in parts if p)


def normalize(actual: str) -> str:
    """比对用归一化：去掉零宽、NBSP 当空格、压缩所有空白。"""
    s = str(actual or "")
    for c in INVISIBLE:
        s = s.replace(c, "")
    s = s.replace(NBSP, " ")
    return re.sub(r"\s+", "", s)


def verify_caption(actual: str, expected: str, min_newlines: int = MIN_NEWLINES) -> dict:
    """回读校验。返回 {ok, problems, newlines, zw, nbsp}，problems 为空即合格。"""
    text = str(actual or "")
    problems: List[str] = []
    zw = sum(text.count(c) for c in INVISIBLE)
    nbsp = text.count(NBSP)
    newlines = text.count("\n")
    if zw:
        problems.append(f"零宽字符 {zw} 个（逐字输入产物）")
    if nbsp:
        problems.append(f"NBSP {nbsp} 个（话题联想分隔符）")
    if newlines < min_newlines:
        problems.append(f"换行只有 {newlines} 个，应为 ≥{min_newlines}（段落被压平）")
    a, e = normalize(text), normalize(expected)
    if a != e:
        i = next((k for k in range(min(len(a), len(e))) if a[k] != e[k]), min(len(a), len(e)))
        problems.append(f"正文不一致（首个差异在第 {i} 字：实际 {a[i:i + 12]!r} vs 期望 {e[i:i + 12]!r}）")
    return {"ok": not problems, "problems": problems, "newlines": newlines, "zw": zw, "nbsp": nbsp}


# ── 写入：一次插入，不逐字符 ────────────────────────────────────
_HTML_INSERT_JS = """
(editor, text) => {
  // 按空行切段，每段一个 <div>：contenteditable 只认块级元素当换行，
  // 直接插裸 \\n 会被折叠成空格（这正是旧写法被压平的机理）。
  editor.innerHTML = '';
  const paragraphs = String(text).split(/\\n{2,}/);
  for (const p of paragraphs) {
    const div = document.createElement('div');
    for (const line of p.split('\\n')) {
      const span = document.createElement('span');
      span.textContent = line;
      div.appendChild(span);
      div.appendChild(document.createElement('br'));
    }
    if (!div.textContent) div.appendChild(document.createElement('br'));
    editor.appendChild(div);
  }
  editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
  return editor.innerText;
}
"""


def read_caption(editor: Any) -> str:
    """读回编辑页当前文本（拿不到就当空串，由 verify 判不合格）。"""
    try:
        return editor.inner_text() or ""
    except Exception as exc:  # noqa: BLE001 — 读不到就是不合格，不抛栈
        logging.getLogger(__name__).warning(f"读取描述编辑器文本失败：{type(exc).__name__}: {exc}")
        return ""


def insert_caption(page: Any, editor: Any, caption: str,
                   logger: Optional[logging.Logger] = None) -> dict:
    """一次性写入描述，逐级降级；返回 {ok, strategy, detail}。

    策略顺序（**原生事件优先**，DOM 直改兜底）：
      1. `native_paragraphs` —— `insert_text` 写段 + `Enter`×2 造段（原生按键 → React 状态同步）；
      2. `insert_text`       —— 整段 `page.keyboard.insert_text`（单次 Input.insertText）；
      3. `html_div`          —— JS 直接改 innerHTML（**只改 DOM，不改 React 状态**：
         2026-11-15 实测出现过「回读正常、发布作品却是『无作品描述』」，所以只当兜底）。
    """
    log = logger or logging.getLogger(__name__)
    expected = caption.strip()

    try:
        editor.click()
        editor.fill("")
    except Exception as exc:  # noqa: BLE001 — 清空失败不致命，下面照写
        log.warning(f"清空描述编辑器失败（继续写）：{type(exc).__name__}: {exc}")

    attempts = []

    # ① 原生按键造段：段间 Enter×2 —— 编辑器自己生成块级节点，React 状态同步
    try:
        editor.fill("")
        segments = [seg for seg in expected.split("\n\n")]
        page.keyboard.insert_text(segments[0])
        for seg in segments[1:]:
            page.keyboard.press("Enter")
            page.keyboard.press("Enter")
            page.keyboard.insert_text(seg)
        attempts.append(("native_paragraphs", read_caption(editor)))
    except Exception as exc:  # noqa: BLE001 — 该策略不可用就降级
        log.warning(f"native_paragraphs 策略失败：{type(exc).__name__}: {exc}")

    # ② 整段 insert_text（单次 Input.insertText）
    try:
        editor.fill("")
        page.keyboard.insert_text(expected)
        attempts.append(("insert_text", read_caption(editor)))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"insert_text 策略失败：{type(exc).__name__}: {exc}")

    # ③ DOM 直改（兜底：能过回读，但可能不进提交态）
    try:
        editor.evaluate(_HTML_INSERT_JS, expected)
        attempts.append(("html_div", read_caption(editor)))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"html_div 策略失败：{type(exc).__name__}: {exc}")

    for strategy, actual in attempts:
        verdict = verify_caption(actual, expected)
        if verdict["ok"]:
            log.info(f"描述写入成功（策略 {strategy}）：换行 {verdict['newlines']}")
            return {"ok": True, "strategy": strategy, "actual": actual, "verify": verdict}
        log.warning(f"策略 {strategy} 回读不合格：{'；'.join(verdict['problems'])}")

    # 兜底：fill
    try:
        editor.fill(expected)
        actual = read_caption(editor)
        verdict = verify_caption(actual, expected)
        if verdict["ok"]:
            log.info("描述写入成功（策略 fill）")
            return {"ok": True, "strategy": "fill", "actual": actual, "verify": verdict}
        attempts.append(("fill", actual))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"fill 策略失败：{type(exc).__name__}: {exc}")

    actual = attempts[-1][1] if attempts else read_caption(editor)
    verdict = verify_caption(actual, expected)
    return {"ok": False, "strategy": "failed", "actual": actual, "verify": verdict}


def describe(verdict: dict) -> str:
    """一行摘要，给日志/回执用。"""
    if verdict.get("ok"):
        return f"ok 换行={verdict['newlines']} 零宽={verdict['zw']} NBSP={verdict['nbsp']}"
    return "⚠️ 简介排版异常：" + "；".join(verdict.get("problems") or ["未知"])
