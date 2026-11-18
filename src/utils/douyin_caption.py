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


def verify_caption(actual: str, expected: str, min_newlines: Optional[int] = None) -> dict:
    """回读校验。返回 {ok, problems, newlines, want_newlines, zw, nbsp}。

    两个真机教训（2026-11-18）：
    1. `min_newlines` 不能写死常量。单段简介本来只有 0 个换行，旧判据硬要求 ≥2 →
       永远红（A5 的 `lines:0 / wantLines:1` 假红就是同一类病）。现在按**期望文本的段数**算。
    2. 零宽字符是**平台自己塞的**（空编辑器占位、话题分隔），只要去掉后正文一致就不该判失败，
       仅作为 `zw` 记录出来；NBSP 同理。
    """
    text = str(actual or "")
    problems: List[str] = []
    zw = sum(text.count(c) for c in INVISIBLE)
    nbsp = text.count(NBSP)
    newlines = text.count("\n")
    exp = str(expected or "")
    want_newlines = max(0, len([s for s in re.split(r"\n+", exp) if s.strip()]) - 1)
    need = want_newlines if min_newlines is None else min_newlines
    if newlines < need:
        problems.append(f"换行只有 {newlines} 个，应为 ≥{need}（段落被压平）")
    a, e = normalize(text), normalize(expected)
    if a != e:
        i = next((k for k in range(min(len(a), len(e))) if a[k] != e[k]), min(len(a), len(e)))
        problems.append(f"正文不一致（首个差异在第 {i} 字：实际 {a[i:i + 12]!r} vs 期望 {e[i:i + 12]!r}）")
    return {"ok": not problems, "problems": problems, "newlines": newlines,
            "want_newlines": need, "zw": zw, "nbsp": nbsp}


# ── 写入：一次插入，不逐字符（html_div 伪兜底已于 R4 实测后删除）───────────



def read_caption(editor: Any) -> str:
    """读回编辑页当前文本（拿不到就当空串，由 verify 判不合格）。"""
    try:
        return editor.inner_text() or ""
    except Exception as exc:  # noqa: BLE001 — 读不到就是不合格，不抛栈
        logging.getLogger(__name__).warning(f"读取描述编辑器文本失败：{type(exc).__name__}: {exc}")
        return ""


def focus_end(page: Any, editor: Any) -> None:
    """把光标放到编辑器内容末尾并聚焦（键盘事件才会打到正文里）。"""
    try:
        editor.click()
    except Exception:  # noqa: BLE001
        pass
    try:
        page.evaluate(
            """(ed) => {
              ed.focus();
              const r = document.createRange();
              r.selectNodeContents(ed);
              r.collapse(false);
              const s = window.getSelection();
              s.removeAllRanges();
              s.addRange(r);
            }""", editor.element_handle())
    except Exception:  # noqa: BLE001 — 拿不到 handle 就只靠 click+focus
        pass


def insert_caption(page: Any, editor: Any, caption: str,
                   logger: Optional[logging.Logger] = None) -> dict:
    """一次性写入描述，逐级降级；返回 {ok, strategy, detail}。

    **R4 真机 A/B 结论（2026-11-18，scripts/probe_douyin_desc_ab.py 实测）**：
    ACE 编辑器吃**真实回车事件**，不吃 HTML 结构——
      · `insert_text` + `Enter` 造段 → `.ace-line` 3/5/7 个，段落完整保留；
      · `execCommand('insertHTML', '<div>…</div>')` → **全部压平成 1 个 `.ace-line`**，
        且 React 提交态为空（历史事故：回读正常、作品却「无作品描述」）。
    所以：原生按键是唯一可靠写法，`html_div` 已删除（它不是兜底，是另一个 bug）。

    策略顺序：
      1. `native_paragraphs` —— `insert_text` 写段 + **单 Enter** 造段
         （单 Enter 已足够分行；双 Enter 会多占一行，而抖音展示区只露 2~3 行）；
      2. `insert_text`       —— 整段一次 `insert_text`（含换行字符，交给编辑器自己处理）；
    """
    log = logger or logging.getLogger(__name__)
    expected = caption.strip()

    try:
        editor.click()
        editor.fill("")
    except Exception as exc:  # noqa: BLE001 — 清空失败不致命，下面照写
        log.warning(f"清空描述编辑器失败（继续写）：{type(exc).__name__}: {exc}")

    attempts = []

    # ① 原生按键造段：段间单 Enter —— 编辑器自己生成 .ace-line，React 状态同步
    #    **每次按键前都把光标钉回编辑器末尾**：真机实测（2026-11-18）先填过「作品标题」
    #    后，焦点可能留在标题框，Enter 就发到别处 —— 正文因此一行都分不开（lines=0）。
    try:
        editor.fill("")
        focus_end(page, editor)
        segments = [seg for seg in expected.split("\n") if seg.strip()]
        page.keyboard.insert_text(segments[0])
        for seg in segments[1:]:
            focus_end(page, editor)
            page.keyboard.press("Enter")
            focus_end(page, editor)
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

    # ③ html_div 兜底已删除：R4 实测它必然把段落压平且不进 React 提交态，
    #    留着只会「回读正常、发布无描述」，属于伪兜底（见本函数 docstring）。

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
