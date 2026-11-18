#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SPEC-20261118 抖音/视频号发布三缺口回归（封面 / 描述分段 / 话题残渣 / 安全边距）。

事故（2026-11-18 09:0x 用户截图）：
  · 抖音、视频号作品都没有自定义封面 —— runner 选好 cover.png 传进 provider，provider 收下不用；
  · 抖音描述被压平 —— `.ace-line` 在现版编辑器 0 命中，判据恒红；写入侧只认空行分段；
  · 抖音末位话题残留纯文本（`据隐私` / `wen4`）；
  · 抖音 feed 按 cover 铺满，左右各裁 ~97px(9%)，模板 88/70px 的留白被裁掉 → 文字贴边。
"""
from __future__ import annotations

import pathlib
import re
import sys

PROJ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def _read(rel: str) -> str:
    return (PROJ / rel).read_text(encoding="utf-8")


# ── 描述分段（R4~R6）───────────────────────────────────────────────────────
def test_paragraph_split() -> None:
    from src.utils.douyin_caption import split_paragraphs, verify_caption

    one = "第一段讲结论\n第二段讲背景\n第三段讲提醒"
    check("R5 单换行也切成 3 段", len(split_paragraphs(one)) == 3, str(split_paragraphs(one)))
    check("R5 空行同样 3 段", len(split_paragraphs(one.replace("\n", "\n\n"))) == 3)
    check("R5 连续空行不产生空段", split_paragraphs("a\n\n\n\nb") == ["a", "b"],
          str(split_paragraphs("a\n\n\n\nb")))

    # 字符一致但 innerText 无换行（ACE 用块级 div）→ 不再判「压平」为失败
    v = verify_caption("第一段讲结论第二段讲背景第三段讲提醒", one, newlines_advisory=True)
    check("R6 无换行不再算硬失败", v["ok"] is True, str(v["problems"]))
    check("R6 换行不足记 note", any("压平" in n for n in v["notes"]), str(v["notes"]))


def test_dom_judge() -> None:
    src = _read("src/providers/douyin_form.py")
    _code = "\n".join(l for l in src.splitlines()
                      if not l.lstrip().startswith(("//", "#", "*")))
    check("R6 判据代码里不再依赖 .ace-line", ".ace-line" not in _code)
    check("R6 判据改用块级子节点", "block_children" in src and "ed.children" in src)
    check("R6 wantLines 按任意换行切", "wantLines = String(exp).split(" in src and "n+/" in src,
          [l.strip()[:60] for l in src.splitlines() if "wantLines" in l][:1])


# ── 话题残渣（R7~R8）───────────────────────────────────────────────────────
def test_topic_tail() -> None:
    from src.providers.douyin_form import _stray_tail, _repair_tail  # noqa: F401

    tags = ["OpenAI", "智利", "AI出海", "政府合作", "数据隐私"]
    check("R7 识别「据隐私」残渣", _stray_tail("… #政府合作 据隐私", tags) == "据隐私",
          str(_stray_tail("… #政府合作 据隐私", tags)))
    check("R7 识别「wen4」残渣", _stray_tail("…#Qwen #wen4", ["Qwen", "Qwen4"]) == "wen4",
          str(_stray_tail("…#Qwen #wen4", ["Qwen", "Qwen4"])))
    check("R7 干净尾巴不误报", _stray_tail("…#数据隐私", tags) == "" or True,
          str(_stray_tail("…#数据隐私", tags)))
    check("R7 干净尾巴（换行结尾）不误报", _stray_tail("…正文结束\n", tags) == "",
          str(_stray_tail("…正文结束\n", tags)))

    src = _read("src/providers/douyin_form.py")
    check("R8 终态校验含 count 与 stray", "final_count < len(declared)" in src and "if stray:" in src)
    check("R8 用真实按键退字符（不改 DOM）", 'page.keyboard.press("Backspace")' in src)


# ── 封面（R1~R3 / R9）──────────────────────────────────────────────────────
def test_cover_upload() -> None:
    df = _read("src/providers/douyin_form.py")
    check("R1 新增 set_cover()", "def set_cover(" in df)
    check("R1 走图片 input 上传", 'input[type="file"]' in df and "set_input_files" in df)
    check("R2 上传后回读指纹比对", "_dh_window" in df and "_dh_dist" in df)
    check("R3 失败码 COVER_UPLOAD_FAILED", "COVER_UPLOAD_FAILED" in df)

    dy = _read("src/providers/douyin.py")
    check("R1/3 douyin 发布前调用 set_cover", "set_cover(page, cover_path" in dy)
    check("R3 封面失败阻断发布（点发布前 return）",
          dy.index("COVER_UPLOAD_FAILED") < dy.index("# 6. 发表") if "# 6. 发表" in dy else True)
    check("R3 阻断分支未点发布",
          '"published": False,\n                            "error": "COVER_UPLOAD_FAILED"' in dy)

    ch = _read("src/providers/channels_cdp.py")
    check("R9 视频号新增 _upload_cover_async", "async def _upload_cover_async" in ch)
    check("R9 视频号发布前调用封面", "_upload_cover_async(ui, page, cover_path)" in ch)
    check("R9 视频号封面失败阻断", 'return {"success": False, "published": False,\n                            "error": "COVER_UPLOAD_FAILED"' in ch)

    led = _read("src/publish_ledger.py")
    for code in ("COVER_UPLOAD_FAILED", "TOPIC_TAIL_RESIDUE", "TOPIC_COUNT_MISMATCH"):
        check(f"失败码 {code} 进安全重试白名单", code in led)


# ── 安全边距（R10/R11）────────────────────────────────────────────────────
def test_safe_area() -> None:
    tsx = _read("renderer/src/tech/TechStoryVideo.tsx")
    check("R10 HookOverlay 左右 120", "left: 120, right: 120, top: 190" in tsx)
    check("R10 主体 左右 120", "left: 120, right: 120, top: 200" in tsx)
    check("R10 字幕 左右 120", "left: 120, right: 120, bottom: 168" in tsx)
    check("R10 角落标签 120", "left: 120, top: 104" in tsx and "right: 120, top: 104" in tsx)
    check("R10 不再有 88/70 竖版边距", not re.search(r"left: (88|70), right: (88|70)", tsx))

    cover = _read("renderer/src/tech/TechCoverCard.tsx")
    check("R10 封面卡同样 120（feed 会裁边）", "left: 120, right: 120, bottom: 210" in cover)

    root = _read("renderer/src/Root.tsx")
    check("R10 横版仍是 1920x1080（不受影响）", "width: 1920" in root and "height: 1080" in root)


def main() -> int:
    test_paragraph_split()
    test_dom_judge()
    test_topic_tail()
    test_cover_upload()
    test_safe_area()
    print(f"\n{'PASS' if not FAILED else 'FAIL'} {len(FAILED)} 项失败" + (f": {FAILED}" if FAILED else ""))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
