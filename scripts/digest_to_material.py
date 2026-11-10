#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""速报条目 → 标准物料（materials JSON）

把 `agents_context/xiao-pin/data/last_digest_snapshot.json` 里的第 N 条速报，
转成 RUNBOOK §3 规定的物料格式，直接喂给 §2 的 runner。

用法:
  # 看当前快照有哪些条目
  python3 scripts/digest_to_material.py --list

  # 第 3 条 → materials/digest-3-<uid>.json
  python3 scripts/digest_to_material.py --index 3

  # 按 unique_id 或 URL 精确取
  python3 scripts/digest_to_material.py --unique-id tw_2119746840887537800

  # 只打印 JSON 不落盘
  python3 scripts/digest_to_material.py --index 3 --stdout

产物字段严格对齐 manifest.schema.json：title ≤20 字（语义重拟，不机械截断）、
body（正文）与 source_url 二选一必有其一。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

SNAPSHOT_CANDIDATES = [
    Path("/Users/a1/Downloads/workspace/team/agents_context/xiao-pin/data/last_digest_snapshot.json"),
    PROJ / "data" / "last_digest_snapshot.json",
]
OUT_DIR = PROJ / "materials"
TITLE_MAX = 20


def default_account() -> str:
    """默认账号从 pipeline.yaml → config/accounts.yaml 解析，禁止在代码里写字面量"""
    import yaml

    py = PROJ / "pipeline.yaml"
    if py.exists():
        try:
            cfg = yaml.safe_load(py.read_text(encoding="utf-8")) or {}
            acc = ((cfg.get("inputs") or {}).get("account") or {}).get("default")
            if acc:
                return str(acc)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN: 读取 pipeline.yaml 失败: {exc}]", file=sys.stderr)

    ay = PROJ / "config" / "accounts.yaml"
    if ay.exists():
        try:
            data = yaml.safe_load(ay.read_text(encoding="utf-8")) or {}
            for a in data.get("accounts") or []:
                if a.get("enabled", True) and a.get("platform") == "douyin":
                    return str(a.get("id"))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN: 读取 accounts.yaml 失败: {exc}]", file=sys.stderr)
    raise RuntimeError("无法确定默认账号：pipeline.yaml / config/accounts.yaml 均未给出")

_URL_RE = re.compile(r"https?://\S+")


def load_snapshot(path: str | None = None) -> tuple[dict, Path]:
    cands = [Path(path)] if path else SNAPSHOT_CANDIDATES
    for p in cands:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data, p
    raise FileNotFoundError(f"速报快照不存在或为空：{[str(c) for c in cands]}")


def clean_body(text: str) -> str:
    """去裸链接、t.co、多余空白，保留可读正文"""
    if not text:
        return ""
    t = _URL_RE.sub("", str(text))
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _pick_title(raw: str, shortener, context: str = "") -> str:
    """标题选择：优先完整短句（不按空格切，避免只剩 'Enterprise' 这种残词）"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) <= TITLE_MAX:
        return raw
    # 先按中文句读切分，取第一个长度合适的完整分句
    for part in re.split(r"[！!？?。；;：:|｜]+", raw):
        part = part.strip()
        # 丢掉被上游截断的残句（含 ... / …），避免把半截话当标题
        if "..." in part or "…" in part:
            continue
        if 8 <= len(part) <= TITLE_MAX:
            return part
    # 混合中英长标题：交给 LLM 语义重拟（≤20 字），失败再退回本地压缩
    sys_prompt = ("你是中文科技媒体标题编辑。把标题改写成不超过20个字的中文标题，"
                  "保留核心主体与动作；禁止引号、禁止省略号（...或…）、禁止编号、禁止截断词。")
    user_prompt = raw if not context else f"标题：{raw}\n正文参考：{context[:120]}"
    for attempt in range(2):
        try:
            from src.llm.client import LLMClient
            cli = LLMClient("llm.fast")
            out = cli.complete(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            cand = str(out or "").strip().strip('"“”').replace("\n", "").strip()
            if ("..." in cand) or ("…" in cand) or not (8 <= len(cand) <= TITLE_MAX):
                print(f"[WARN: 标题候选不合格（第{attempt+1}次）: {cand!r}]", file=sys.stderr)
                continue
            return cand
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN: 标题 LLM 重拟失败: {exc}]", file=sys.stderr)
            break
    # 再退回语义重拟（其内部有前缀/后缀/长词压缩策略）
    return shortener(raw, max_chars=TITLE_MAX)


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in (s or ""))


def to_material(item: dict, account: str | None = None) -> dict:
    from src.utils.text_sanitizer import ensure_clean_short_title
    from src.crawlers.atomic_fetchers import translate_to_zh, _clean_desc_text, _overlap_ratio

    raw_title = str(item.get("title") or "").strip()
    # 平台前缀（如「推特先锋: @xxx: 」）先剥离再重拟，避免把账号名塞进标题
    raw_title = re.sub(r"^(推特先锋|社区热议|最新速报|突发爆料|开源爆款)[：:\s]*", "", raw_title)
    raw_title = re.sub(r"^@[\w\.\-]+[：:\s]+", "", raw_title)

    account = account or default_account()
    body = clean_body(item.get("desc") or "")
    url = str(item.get("url") or "").strip()

    # 英文源（官方博客/X）先中文化，再做标题重拟
    if not _has_cjk(raw_title):
        zh = translate_to_zh(raw_title)
        if zh and _has_cjk(zh):
            raw_title = zh
    if body:
        body = _clean_desc_text(body)   # 去重复句 / 标点整形 / 去噪声
    if body and not _has_cjk(body):
        zh_body = translate_to_zh(body)
        if zh_body and _has_cjk(zh_body):
            body = zh_body

    title = _pick_title(raw_title, ensure_clean_short_title, context=body)

    # 标题过短/过于泛化（如 "Introducing"）时，用正文首句补足语义
    if len(title) < 8:
        cand = clean_body(body).split("\n")[0]
        cand = re.sub(r"^(介绍|Introducing|Announcing|Update|News)[：:\s]+", "", cand, flags=re.I)
        if cand and len(cand) >= 8:
            title = ensure_clean_short_title(cand, max_chars=TITLE_MAX) or title

    if not title:
        raise ValueError("该条目没有可用标题")

    # 正文去重：相邻句高度重合（RSS 常见「标题句 + 标题句扩写」）只留一句
    if body:
        _sents = [x for x in re.split(r"(?<=[。！？!?])", body) if x.strip()]
        _kept: list[str] = []
        for _s in _sents:
            if _kept and _overlap_ratio(_kept[-1], _s) > 0.6:
                continue
            _kept.append(_s)
        body = "".join(_kept).strip()
        # 首句若与标题高度重合，说明只是把标题又说了一遍 → 去掉
        if _kept and _overlap_ratio(title, _kept[0]) > 0.55:
            body = "".join(_kept[1:]).strip()

    material: dict = {"title": title, "account": account}
    if body:
        material["body"] = body
    if url:
        material["source_url"] = url
    if not body and not url:
        raise ValueError("该条目既无正文也无链接，无法作为物料")

    # 溯源信息（runner 会忽略未声明字段，但便于人工核对）
    material["_digest"] = {
        "badge": item.get("badge"),
        "published_at": item.get("published_at"),
        "unique_id": item.get("unique_id"),
        "source": item.get("source"),
    }
    return material


def pick(snapshot: dict, index: str | None, unique_id: str | None, url: str | None) -> tuple[str, dict]:
    if index:
        key = str(index).strip()
        if key not in snapshot:
            raise KeyError(f"快照里没有第 {key} 条（共 {len(snapshot)} 条）")
        return key, snapshot[key]
    for k, v in snapshot.items():
        if unique_id and str(v.get("unique_id") or "") == unique_id:
            return k, v
        if url and str(v.get("url") or "") == url:
            return k, v
    raise KeyError(f"未找到匹配条目（unique_id={unique_id} url={url}）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="速报条目 → 标准物料 JSON")
    ap.add_argument("--snapshot", help="指定快照文件（默认自动探测）")
    ap.add_argument("--index", help="速报编号，如 3")
    ap.add_argument("--unique-id", help="按 unique_id 精确取")
    ap.add_argument("--url", help="按原文链接精确取")
    ap.add_argument("--account", default=None, help="账号（默认取 pipeline.yaml 的 inputs.account.default）")
    ap.add_argument("--out", help="输出路径（默认 materials/digest-<idx>-<uid>.json）")
    ap.add_argument("--stdout", action="store_true", help="只打印 JSON，不落盘")
    ap.add_argument("--list", action="store_true", help="列出快照全部条目")
    args = ap.parse_args(argv)

    snapshot, snap_path = load_snapshot(args.snapshot)

    if args.list:
        print(f"# 快照：{snap_path}（{len(snapshot)} 条）")
        for k in sorted(snapshot, key=lambda x: int(x) if str(x).isdigit() else 999):
            v = snapshot[k]
            print(f"{k:>3}. [{v.get('badge') or v.get('source') or '-'}] {str(v.get('title'))[:46]}")
        return 0

    if not (args.index or args.unique_id or args.url):
        ap.error("需要 --index / --unique-id / --url 之一，或用 --list 查看")

    idx, item = pick(snapshot, args.index, args.unique_id, args.url)
    material = to_material(item, args.account)

    payload = json.dumps(material, ensure_ascii=False, indent=2)
    if args.stdout:
        print(payload)
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    uid = re.sub(r"[^\w\-]+", "_", str(item.get("unique_id") or "item"))[:40]
    out = Path(args.out) if args.out else OUT_DIR / f"digest-{idx}-{uid}.json"
    out.write_text(payload + "\n", encoding="utf-8")

    print(f"MATERIAL_OK path={out}")
    print(f"  title={material['title']} ({len(material['title'])}字)")
    print(f"  body_chars={len(material.get('body') or '')} | source_url={'有' if material.get('source_url') else '无'}")
    print(f"  digest={material['_digest']}")
    print("\n下一步：")
    print(f"  DISPATCH_MODE=output_only /opt/homebrew/opt/python@3.14/bin/python3.14 \\")
    print(f"    -m src.pipeline.runner --material {out} --run-id digest-{idx}-{uid[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
