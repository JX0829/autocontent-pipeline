#!/usr/bin/env python3
"""发布缺口离线回归（封面自证 / 描述分段判据 / 话题残渣 / 安全边距）。

对应 SPEC-20261118 R1~R12、A1~A9 的**可离线复现**部分；真机部分由
`scripts/verify_publish_gaps.py` 与 `scripts/measure_safe_area.py` 出证据。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

RESULTS: list[tuple[str, bool, str]] = []
VERBOSE = "-v" in sys.argv


def check(label: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((label, bool(cond), detail))
    if VERBOSE or not cond:
        print(f"{'PASS' if cond else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))


def _png(color: str, size=(540, 960), stripes: int = 0) -> bytes:
    """纯色图 dHash 全 0（任意两张纯色图都会被判「同图」），所以要带结构。"""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", size, color)
    if stripes:
        d = ImageDraw.Draw(im)
        step = max(2, size[0] // stripes)
        for i in range(0, size[0], step):
            d.rectangle([i, 0, i + step // 2, size[1]], fill="#ffffff")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_img_digest() -> None:
    from src.utils.img_digest import dhash, same_image

    a = _png("#203040", stripes=9)
    b = _png("#f0e0d0", stripes=3)
    check("dhash 确定性（同字节同值）", dhash(a) == dhash(a))
    check("同图判定通过", same_image(a, a)[0] is True)
    ok, dist = same_image(a, b)
    check("异图判定不通过", ok is False, f"dist={dist}")


def test_caption_dom_judge() -> None:
    """A2/A5：判据必须能真实区分 1 段和 3 段，且 wantLines 不再恒为 1。"""
    import src.providers.douyin_form as df

    class _P:
        def __init__(self, payload):
            self.payload = payload

        def evaluate(self, _js):
            return self.payload

    one = _P({"lines": 1, "wantLines": 1, "flatLen": 30})
    three = _P({"lines": 3, "wantLines": 3, "flatLen": 90})
    check("单段 → lines=1", df.caption_dom(one)["lines"] == 1)
    check("三段 → lines=3（判据能区分）", df.caption_dom(three)["lines"] == 3)
    check("三段时 wantLines=3（不再恒 1）", df.caption_dom(three)["wantLines"] == 3)

    class _Boom:
        def evaluate(self, _js):
            raise RuntimeError("no page")

    check("page 异常 → 返回可判定的空结构（不抛）", df.caption_dom(_Boom())["lines"] == 0)


def test_topic_tail() -> None:
    """R7/R8：尾部残渣识别 + 终态自证。"""
    import src.providers.douyin_form as df

    class _P:
        def __init__(self, residue: str, count: int):
            self.residue, self.count = residue, count
            self.presses = 0

        def evaluate(self, js):
            # 数话题的 JS 里没有 createRange；读残渣的 JS 有 → 用它区分两种调用
            return self.count if "createRange" not in js else self.residue

        def wait_for_timeout(self, _ms):
            return None

        class keyboard:  # noqa: N801
            @staticmethod
            def press(_k):
                pass

    p = _P("据隐私", 6)
    res = df.clean_topic_tail(p, ["数据隐私", "OpenAI"])
    check("残渣识别为某话题后缀 → 执行清理", res["cleaned"] is True, json.dumps(res, ensure_ascii=False)[:80])

    p2 = _P("完全无关的话", 6)
    res2 = df.clean_topic_tail(p2, ["数据隐私"])
    check("非话题后缀的尾部文本 → 不动它", res2["cleaned"] is False, res2["after"])

    p3 = _P("", 5)
    fin = df.verify_topics(p3, ["a", "b", "c", "d", "e"])
    check("真话题数 == 声明数且无残留 → 通过", fin["ok"] is True, json.dumps(fin, ensure_ascii=False))
    p4 = _P("wen4", 1)          # 声明 2 个、只落地 1 个，且尾部有残渣 → 两个问题都要报
    fin4 = df.verify_topics(p4, ["Qwen4", "x"])
    check("残渣 + 数量不足 → 不通过（两条问题都报出）",
          fin4["ok"] is False and len(fin4["problems"]) == 2,
          json.dumps(fin4, ensure_ascii=False))


def test_cover_entry_missing() -> None:
    """A6：封面入口找不到 → 明确失败，调用方据此 COVER_UPLOAD_FAILED。"""
    import src.providers.douyin_form as df

    class _L:
        def count(self):
            return 0

        def first(self):
            return self

        def is_visible(self):
            return False

    class _Page:
        def locator(self, *_a, **_k):
            return _L()

    with tempfile.TemporaryDirectory() as td:
        cov = Path(td) / "cover.png"
        cov.write_bytes(_png("#123456"))
        d = df.set_cover(_Page(), str(cov))
    check("入口缺失 → ok=False 且文案明确", d["ok"] is False and "找不到封面入口" in str(d["error"]),
          str(d.get("error"))[:50])

    d2 = df.set_cover(_Page(), "/tmp/definitely-not-exists-cover.png")
    check("封面文件不存在 → 直接失败", d2["ok"] is False and "不存在" in str(d2["error"]))


def test_renderer_safe_x() -> None:
    """F5/R10：竖版模板不允许残留 88/70 的左右边距硬编码。"""
    src = (PROJ / "renderer/src/tech/TechStoryVideo.tsx").read_text(encoding="utf-8")
    check("模板定义了模块级 SAFE_X=120", "const SAFE_X = 120;" in src)
    check("无 left/right 88 残留", "left: 88" not in src and "right: 88" not in src)
    check("无 left/right 70 残留", "left: 70" not in src and "right: 70" not in src)
    check("字幕框 maxWidth 随 SAFE_X 收窄", "1080 - SAFE_X * 2" in src)


def main() -> int:
    test_img_digest()
    test_caption_dom_judge()
    test_topic_tail()
    test_cover_entry_missing()
    test_renderer_safe_x()
    ok = sum(1 for _, c, _ in RESULTS if c)
    bad = [(l, d) for l, c, d in RESULTS if not c]
    print(f"\nPUBLISH GAPS (offline) {ok} PASS / {len(bad)} FAIL")
    for l, d in bad:
        print(f"  ✗ {l} {d}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
