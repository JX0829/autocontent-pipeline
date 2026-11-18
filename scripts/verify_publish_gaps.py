#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布缺口真机验收（A1~A6 / A7 / A9 的表单态与像素证据）。

**不发布**：两个平台都走 `stop_before_publish`，只上传+填表+回读，拿到的是后台表单证据。

用法：
  python3 scripts/verify_publish_gaps.py --run-dir output/20261118/openai-chile-1 \
      --platforms douyin,channels [--no-shot]

输出：逐项 A 编号 → PASS/FAIL + 实测值；截图落在 <run>/gaps_evidence/。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from src.utils.img_digest import same_image  # noqa: E402


def _load_runs(run: Path) -> dict:
    draft = json.loads((run / "draft_script.json").read_text(encoding="utf-8"))
    return {
        "title": str(draft.get("title") or "").strip(),
        "desc": str(draft.get("video_desc") or draft.get("body") or "").strip(),
        "tags": [str(t).lstrip("#") for t in (draft.get("tags") or [])],
        "video": str(run / "video.mp4"),
        "cover": str(run / "cover.png"),
    }


def _account(platform: str) -> dict:
    import yaml

    cfg = yaml.safe_load((PROJ / "config/accounts.yaml").read_text(encoding="utf-8"))
    accs = cfg.get("accounts") or cfg if isinstance(cfg, list) else cfg.get("accounts", [])
    for a in accs:
        if a.get("platform") == platform:
            return a
    raise SystemExit(f"accounts.yaml 里没有 {platform} 账号")


def check(label: str, ok: bool, detail: str = "") -> dict:
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f" | {detail}" if detail else ""))
    return {"a": label, "ok": bool(ok), "detail": detail}


def verify_douyin(run: Path, info: dict, results: list[dict]) -> dict:
    from src.providers.douyin import DouyinProvider

    prov = DouyinProvider(_account("douyin"))
    res = prov.save_draft(info["video"], info["cover"], info["title"], info["tags"],
                          info["desc"], declare_aigc=True, stop_before_publish=True)
    (run / "gaps_evidence").mkdir(parents=True, exist_ok=True)
    (run / "gaps_evidence" / "douyin_result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    # A6 的正面证据在这里；A1 用 dHash 证明封面就是 cover.png
    cov = res.get("cover") or {}
    results.append(check("A1 抖音封面上传并自证（dHash ≤12）",
                         bool(cov.get("ok")) and int(cov.get("dhash_dist", 99)) <= 12,
                         f"entry={cov.get('entry')} dist={cov.get('dhash_dist')} "
                         f"confirm={cov.get('confirm')}"))
    # A2/A4/A5 来自表单回读
    filled = str(res.get("filled_text") or "")
    segs = len([s for s in re.split(r"\n+", info["desc"]) if s.strip()])
    got_lines = int((res.get("caption_dom") or {}).get("lines") or 0)
    results.append(check("A2 抖音描述分段（DOM 非空行数 ≥ 文案段数）",
                         got_lines >= segs, f"lines={got_lines} want≥{segs}"))
    tail = str((res.get("topics_tail") or {}).get("after") or "")
    results.append(check("A3 抖音描述尾部无游离文本", not tail.strip(), f"tail={tail!r}"))
    fin = res.get("topics_final") or {}
    # 方案A：无联想项的话题直接丢弃 → 「声明」只算真落地的，丢弃的单独列在证据里
    dropped = res.get("topics_dropped_no_suggestion") or []
    results.append(check("A4 抖音真话题数 == 落地声明数（无联想项已丢弃）",
                         int(fin.get("count", 0)) == int(fin.get("want", 0)) > 0,
                         f"count={fin.get('count')} want={fin.get('want')} "
                         f"dropped={dropped}"))
    results.append(check("A5 不再出现 lines:0 + wantLines:1 假红",
                         not (got_lines == 0 and (res.get("caption_dom") or {}).get("wantLines") == 1),
                         json.dumps(res.get("caption_dom") or {}, ensure_ascii=False)[:120]))
    results.append(check("A2/A3/A4 未阻断（stop_before_publish 走到位）",
                         res.get("status") == "STOPPED_BEFORE_PUBLISH"
                         or res.get("success") is True, str(res.get("status") or res.get("error"))))
    return res


def verify_channels(run: Path, info: dict, results: list[dict]) -> dict:
    from src.providers.channels_cdp import ChannelsCDPProvider

    prov = ChannelsCDPProvider(_account("channels"))
    res = prov.publish_live(info["video"], info["cover"], info["title"], info["desc"],
                            info["tags"], stop_before_publish=True)
    ev = run / "gaps_evidence"
    ev.mkdir(parents=True, exist_ok=True)
    (ev / "channels_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    cov = res.get("cover") or {}
    results.append(check("A7 视频号封面上传并自证（dHash ≤12）",
                         bool(cov.get("ok")) and int(cov.get("dhash_dist", 99)) <= 12,
                         f"entry={cov.get('entry')} dist={cov.get('dhash_dist')} "
                         f"confirm={cov.get('confirm')}"))
    return res


def verify_a6_negative(info: dict, results: list[dict]) -> None:
    """A6 反向：封面入口不可用 → 必须 COVER_UPLOAD_FAILED 且不进发布。"""
    from src.providers import douyin_form

    class _Fake:
        def locator(self, *_a, **_k):
            class _L:
                def count(self):
                    return 0

                def first(self):
                    return self

                def is_visible(self):
                    return False
            return _L()

    d = douyin_form.set_cover(_Fake(), info["cover"])
    results.append(check("A6 封面入口缺失 → 明确失败（调用方据此阻断）",
                         d.get("ok") is False and "找不到封面入口" in str(d.get("error")),
                         str(d.get("error"))[:60]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--platforms", default="douyin,channels")
    ap.add_argument("--tags-override", default="",
                    help="逗号分隔的话题列表，覆盖物料 tags（用来验「无联想项直接丢弃」这类分支）")
    ap.add_argument("--skip-cover", action="store_true",
                    help="不传封面：单独验描述/话题链路（封面自证未通过时用，避免被它挡住）")
    a = ap.parse_args()

    run = Path(a.run_dir).resolve()
    info = _load_runs(run)
    if a.skip_cover:
        info["cover"] = ""
    if a.tags_override:
        info["tags"] = [x.strip() for x in a.tags_override.split(",") if x.strip()]
    print(f"run={run.name} title={info['title'][:24]!r} tags={len(info['tags'])} "
          f"cover={Path(info['cover']).exists()}")
    results: list[dict] = []
    plats = [p.strip() for p in a.platforms.split(",") if p.strip()]

    verify_a6_negative(info, results)
    if "douyin" in plats:
        verify_douyin(run, info, results)
    if "channels" in plats:
        verify_channels(run, info, results)

    (run / "gaps_evidence" / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r["ok"])
    print(f"\nGAPS EVIDENCE {ok} PASS / {len(results) - ok} FAIL")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
