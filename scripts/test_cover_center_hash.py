#!/usr/bin/env python3
"""封面回读口径单测：平台预览 vs cover.png，必须「同图判同、异图判异」。

为什么需要这个口径（2026-11-19 真机事故）：
  抖音发布页的封面预览是**小尺寸 + 白边框/圆角 + 与 cover 不同宽高比**的缩略图。
  拿它跟 1080×1920 的 cover.png 直接比 dHash：同一张封面也要 16~17（阈值 14）→ 误判「没上传」，
  整条片发不出去。修法两步：
    ① 先把 cover.png 按预览宽高比**中心裁**，消灭比例差；
    ② 两边再各取**中心 84%**，躲开白边框/圆角那圈像素。
离线实测：同一张 5、另一条片的封面 25~28 → 阈值 14 分得开。

跑法：python3 scripts/test_cover_center_hash.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from PIL import Image  # noqa: E402

from src.providers.douyin_form import COVER_DH_MAX, _dh_dist, _dh_window  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def center_crop(im: Image.Image, target: float) -> Image.Image:
    """按目标宽高比中心裁（与 set_cover 里的算法同一份口径）。"""
    W, H = im.size
    if W / float(H) > target:
        nw, nh = int(round(H * target)), H
    else:
        nw, nh = W, int(round(W / target))
    left, top = (W - nw) // 2, (H - nh) // 2
    return im.crop((left, top, left + nw, top + nh))


def inner(im: Image.Image, frac: float = 0.84) -> Image.Image:
    w, h = im.size
    nw, nh = max(8, int(w * frac)), max(8, int(h * frac))
    l, t = (w - nw) // 2, (h - nh) // 2
    return im.crop((l, t, l + nw, t + nh))


def fake_preview(cover: Path, dst: Path, box: tuple[int, int] = (211, 376),
                 border: float = 0.06) -> Path:
    """把 cover 仿成「平台预览缩略图」：中心裁到预览比例 → 缩到小尺寸 → 加白边框 → 存盘。"""
    with Image.open(cover) as im:
        im = im.convert("RGB")
        small = center_crop(im, box[0] / float(box[1])).resize(box, Image.LANCZOS)
    bw = max(2, int(min(box) * border))
    canvas = Image.new("RGB", (box[0] + bw * 2, box[1] + bw * 2), (255, 255, 255))
    canvas.paste(small, (bw, bw))
    canvas.save(dst)
    return dst


def dist_center84(preview: Path, cover: Path) -> int:
    """完全按 set_cover 的口径算距离。"""
    with Image.open(preview) as pv, Image.open(cover) as cv:
        target = pv.size[0] / float(pv.size[1])
        cropped = center_crop(cv.convert("RGB"), target)
    tmp = Path(tempfile.mkdtemp())
    pv_inner = tmp / "pv_inner.png"
    cv_inner = tmp / "cv_inner.png"
    with Image.open(preview) as pv:
        inner(pv.convert("RGB")).save(pv_inner)
    inner(cropped).save(cv_inner)
    return _dh_dist(_dh_window(str(pv_inner)), _dh_window(str(cv_inner)))


def dist_raw(preview: Path, cover: Path) -> int:
    """改之前的口径：整图直接比。"""
    return _dh_dist(_dh_window(str(preview)), _dh_window(str(cover)))


def main() -> int:
    covers = sorted(Path(PROJ / "output").glob("*/*/cover.png"))
    if len(covers) < 2:
        print("SKIP：output 里不足两张 cover.png，无法做同图/异图对照")
        return 0
    a, b = covers[0], covers[1]
    tmp = Path(tempfile.mkdtemp())
    pa = fake_preview(a, tmp / "pv_a.png")
    pb = fake_preview(b, tmp / "pv_b.png")

    raw_same = dist_raw(pa, a)
    new_same = dist_center84(pa, a)
    new_diff = dist_center84(pa, b)

    print(f"夹具：{a.relative_to(PROJ)} / {b.relative_to(PROJ)}")
    print(f"  旧口径（整图比）同图距离 : {raw_same}")
    print(f"  新口径（中心84%）同图距离: {new_same}")
    print(f"  新口径 异图距离          : {new_diff}")

    check(f"同图（含白边框预览）新口径距离 ≤ {COVER_DH_MAX}", new_same <= COVER_DH_MAX, f"dist={new_same}")
    check(f"异图新口径距离 > {COVER_DH_MAX}", new_diff > COVER_DH_MAX, f"dist={new_diff}")
    check("新口径比旧口径明显收紧", new_same < raw_same, f"new={new_same} raw={raw_same}")
    check("异图 / 同图 有足够间隔（≥2 倍）", new_diff >= max(2 * new_same, COVER_DH_MAX + 1),
          f"diff={new_diff} same={new_same}")

    print(f"\nCOVER CENTER-HASH {PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
