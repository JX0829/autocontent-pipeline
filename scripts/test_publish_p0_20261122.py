#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布链路 P0 回归（2026-11-22）：抖音不再 click + 视频号不再静默。

抖音：select_privacy 已选中还 click、清空描述编辑器 click —— 两处都卡 30s。
      根因同 fill_title：上传页持续动效，Playwright 可点击判据永不满足。
视频号：以前失败时日志一点异常都没有，只有一句「未取得平台侧证据」。
"""
import sys
from pathlib import Path

PROJ = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJ))

from src.utils.douyin_caption import clear_caption_editor, focus_caption_editor  # noqa: E402

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


def read(p: str) -> str:
    return (PROJ / p).read_text(encoding="utf-8")


class FakePage:
    """click() 会炸（复现线上 30s 超时），focus 走 JS。"""

    def __init__(self):
        self.clicks = 0
        self.keys = []

    def evaluate(self, js, arg=None):
        if "click" in js and "querySelector" not in js:
            raise AssertionError("不该用 JS click")
        return True

    @property
    def keyboard(self):
        outer = self

        class KB:
            def press(self, k):
                outer.keys.append(k)

            def insert_text(self, t):
                outer.keys.append(f"text:{t[:8]}")
        return KB()

    def wait_for_timeout(self, ms):
        return None

    def locator(self, sel):
        raise AssertionError("不该在清空流程里用 locator.click()")


def real_click_calls(src: str) -> list[str]:
    """只找真正的代码调用（注释/文档串里提到 editor.click() 不算）。"""
    out = []
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("#") or s.startswith('"""') or "`" in s or "2026-11-22" in s:
            continue
        if "editor.click()" in s and not s.startswith("def "):
            out.append(s)
    return out


def main() -> int:
    # ── 抖音：清空描述不再 click ──────────────────────────────
    cap = read("src/utils/douyin_caption.py")
    check("① 描述清空走 JS focus（新增 clear_caption_editor）",
          "def clear_caption_editor" in cap and "def focus_caption_editor" in cap)
    check("① 文件内不再有实际的 editor.click() 调用", not real_click_calls(cap),
          str(real_click_calls(cap)))
    check("① 不再用 editor.fill(\"\") 清空", 'editor.fill("")' not in cap)

    form = read("src/providers/douyin_form.py")
    check("① fill_caption 的 _clear 改用 clear_caption_editor",
          "clear_caption_editor(page, logger)" in form)
    check("① fill_caption 不再有实际的 editor.click() 调用", not real_click_calls(form),
          str(real_click_calls(form)))

    dy = read("src/providers/douyin.py")
    check("① select_privacy 先读 checked 再决定是否点", "checked" in dy.split("def select_privacy")[1][:2000])
    check("① select_privacy 已选中直接返回（不点击）",
          "already" in dy.split("def select_privacy")[1][:2000]
          or "已选中" in dy.split("def select_privacy")[1][:2000])

    # 运行时：clear 不点、只按原生键
    page = FakePage()
    ok = clear_caption_editor(page, None)
    check("① 运行时清空成功且用了 Backspace", ok and "Backspace" in page.keys, str(page.keys))
    ok2 = focus_caption_editor(page)
    check("① focus 走 JS 且成功", ok2 is True)

    # ── 视频号：不再静默 ─────────────────────────────────────
    ch = read("src/providers/channels_cdp.py")
    check("② publish_live 有异常兜底（logger.exception）", "logger.exception(" in ch)
    check("② 返回结构化异常（PROVIDER_EXCEPTION + stage）",
          "PROVIDER_EXCEPTION" in ch and '"stage": stage' in ch)
    check("② 成败都落一行结论日志", "[CHANNELS] 发布结束" in ch)
    stages = [s for s in ("upload", "wait_editor", "fill_copy", "declare_original",
                          "declare_aigc", "click_publish", "verify_dom", "readback")
              if f'self._publish_stage = "{s}"' in ch]
    check("② 8 个步骤都有 stage 标记", len(stages) == 8, f"{len(stages)}/8")
    check("② 回读失败不是含糊 unverified（区分 FAILED_NOT_PUBLISHED）",
          "FAILED_NOT_PUBLISHED" in ch)

    print(f"\nPASS {PASS} 项" + (f"｜FAIL {len(FAIL)}: {FAIL}" if FAIL else "｜0 项失败"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
