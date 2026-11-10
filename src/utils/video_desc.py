#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三平台统一简介生成（B站 / 抖音 / 视频号）。

设计目标（2026-11-10 定稿）：
1. **人话优先**：简介是给人读的，不能出现 `/tdd`、`/grill-me` 这类斜杠命令，
   也不能直接搬分镜台词和文件名。
2. **三平台同源**：三个平台的正文都从本模块出，避免各脚本各写一套导致
   视频号只有标题、抖音堆命令、B站又另一种风格。
3. **老片兼容**：老 run 没有 `video_desc` 字段时，用 LLM 现生成一次并缓存到
   `output/<date>/<run_id>/description.txt`，之后复用，不重复烧 token。
4. **兜底可用**：LLM 不可用时，用 `humanize()` 把 `fact_summary_150` /
   分镜旁白清洗成可读文本（去命令、去文件名、去代码符号）。

平台格式差异：
- 视频号：`正文\\n\\n#标签`
- 抖音  ：`标题 #标签\\n正文`（标题必须在首行，抖音发布器按首行识别）
- B站   ：`正文\\n\\n#标签`（标题单独走 title 字段）
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("VideoDesc")

# 常见斜杠命令 → 人话（按长度倒序匹配，避免 /grill-me 被 /grill 抢先吃掉）
CMD_MAP = {
    "grill-with-docs": "先追问需求并沉淀文档",
    "diagnosing-bugs": "单独排查疑难问题",
    "improve-codebase-architecture": "重构代码结构",
    "codebase-design": "设计代码结构",
    "domain-modeling": "梳理领域模型",
    "implement-spec": "按规格实现",
    "code-review": "做代码评审",
    "grill-me": "追问需求",
    "to-tickets": "拆成任务单",
    "to-spec": "写成规格文档",
    "wayfinder": "梳理实现路径",
    "prototype": "做原型验证",
    "triage": "做问题分诊",
    "tdd": "走测试驱动开发",
    "retro": "做复盘",
    "research": "做技术调研",
    "implement": "落地实现",
    "ask-matt": "咨询作者",
    "wizard": "引导式生成",
    "pr": "提交合并请求",
}

FILE_WORDS = {
    "CONTEXT.md": "上下文文档",
    "ADR": "架构决策记录",
}

_CMD_RE = re.compile(r"(?<![\w/])/([a-z][a-z0-9-]*)")
_FILE_RE = re.compile(r"\b[A-Za-z0-9_-]+\.(?:md|py|ts|tsx|js|json|sh|yaml|yml|txt)\b")
_SNAKE_RE = re.compile(r"\b[a-z]{2,}(?:_[a-z0-9]+){1,}\b")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _tidy(text: str) -> str:
    """收尾清洗：多余空格、连续标点、命令删掉后留下的悬空标点。"""
    t = re.sub(r"[ \t]{2,}", " ", text)
    t = re.sub(r"：\s*[，、；]", "：", t)
    t = re.sub(r"([，、；])\s*([，、；。])", r"\2", t)
    t = re.sub(r"（\s*）|\(\s*\)", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _collapse_repeats(text: str) -> str:
    """命令替换后常出现同义重复（"追问需求 或 先追问需求并沉淀文档 追问需求"），
    按逗号/分号切分后做近似去重，保留信息量更大的一条。"""
    parts = re.split(r"([，。；])", text)
    out, last_clause = [], ""
    for chunk in parts:
        if chunk in "，。；":
            out.append(chunk)
            continue
        clause = chunk.strip()
        if not clause:
            out.append(chunk)
            continue
        if last_clause:
            a, b = set(last_clause), set(clause)
            if a and b and len(a & b) / min(len(a), len(b)) >= 0.6:
                # 近似重复：保留更长的（信息更多）那条，替换掉上一段
                if len(clause) > len(last_clause):
                    for i in range(len(out) - 1, -1, -1):
                        if out[i].strip() == last_clause:
                            out[i] = clause
                            break
                    last_clause = clause
                continue
        out.append(chunk)
        last_clause = clause
    return "".join(out)


def _dedupe_words(clause: str) -> str:
    """同一分句内命令替换后的词级去重：
    "追问需求 或 先追问需求并沉淀文档 先追问需求" → "追问需求并沉淀文档"
    """
    tokens = [t for t in clause.split(" ") if t]
    if len(tokens) < 2:
        return clause
    kept: list[str] = []
    for tok in tokens:
        if tok in ("或", "、", "和", "与"):
            continue
        dup = False
        for i, k in enumerate(kept):
            if tok == k or tok in k or k in tok:
                if len(tok) > len(k):
                    kept[i] = tok
                dup = True
                break
        if not dup:
            kept.append(tok)
    return " ".join(kept)


def humanize(text: str) -> str:
    """把含命令/文件名的技术文本清洗成人话（兜底用，不追求文采）。"""
    if not text:
        return ""
    t = text
    for k, v in FILE_WORDS.items():
        t = t.replace(k, v)
    t = _FILE_RE.sub("", t)
    # 成对出现的命令先合并，避免替换后同义重复
    t = re.sub(r"/grill-me\s*(?:或|、|/)\s*/?grill-with-docs", "追问需求并沉淀文档", t)
    t = re.sub(r"/grill-with-docs\s*(?:或|、|/)\s*/?grill-me", "追问需求并沉淀文档", t)
    for k in sorted(CMD_MAP, key=len, reverse=True):
        t = re.sub(rf"(?<![\w/])/{re.escape(k)}\b", CMD_MAP[k], t)
    t = _CMD_RE.sub("", t)          # 未登记的命令直接去掉
    t = _SNAKE_RE.sub("", t)
    t = _EMOJI_RE.sub("", t)
    t = t.replace("~/", "").replace("()", "")
    t = re.sub(r"\s*或\s*(?=[，。；])", "", t)
    t = re.sub(r"([，。；])\s*或\s*", r"\1", t)
    t = re.sub(r"([，。；])\s*", r"\1", t)
    # 逐分句做词级去重
    parts = re.split(r"([，。；])", t)
    t = "".join(p if p in "，。；" else _dedupe_words(p) for p in parts)
    return _tidy(_collapse_repeats(t))


def _draft_desc(draft: dict) -> str:
    """draft 里已有的简介：video_desc 优先，其次清洗 fact_summary，最后拼分镜旁白。"""
    vd = str(draft.get("video_desc") or "").strip()
    if vd:
        return humanize(vd) if "/" in vd else vd
    for key in ("full_body", "summary"):
        v = str(draft.get(key) or "").strip()
        if v:
            return humanize(v)
    fact = str(draft.get("fact_summary_150") or "").strip()
    if fact:
        return humanize(fact)
    narr = [str(s.get("segment_narration") or "").strip() for s in (draft.get("scenes") or [])]
    narr = [n for n in narr if n and "欢迎在评论区" not in n]
    return humanize("".join(narr[:3]))


def generate_desc(draft: dict, llm=None) -> str:
    """调 LLM 生成人话简介（100~170 字，无命令/无 emoji/无标签）。失败返回空串。"""
    try:
        if llm is None:
            from src.llm.client import LLMClient
            llm = LLMClient("llm.main")
        facts = "\n".join(f"- {s.get('segment_narration', '')}" for s in (draft.get("scenes") or []))
        prompt = (
            "把下面这条科技短视频写成一段**给人读的简介**（100~170字）：\n"
            "- 自然口语化的书面语，像给朋友介绍，不要罗列要点\n"
            "- 严禁出现斜杠命令（如 /tdd、/grill-me）、文件名、代码符号\n"
            "- 不要标题党，不要 emoji，不要话题标签，不要「本视频」「点击」等话术\n"
            "- 结尾不要引导语\n\n"
            f"视频标题：{draft.get('title', '')}\n事实要点：\n{facts}\n"
            f"摘要：{draft.get('fact_summary_150', '')}\n\n只输出简介正文。"
        )
        out = llm.complete([{"role": "user", "content": prompt}], temperature=0.5, task="video_desc")
        if isinstance(out, dict):
            out = next((v for v in out.values() if isinstance(v, str)), "")
        text_raw = str(out or "").strip()
        # 本地网关会强制 JSON 模式，返回 {"text": "..."} 这类字符串
        if text_raw.startswith("{"):
            try:
                parsed = json.loads(text_raw)
                if isinstance(parsed, dict):
                    text_raw = next((v for v in parsed.values() if isinstance(v, str) and v.strip()), text_raw)
            except json.JSONDecodeError:
                logger.warning("VIDEO_DESC_JSON_PARSE_FAILED: 保留原始文本")
        text = humanize(text_raw)
        return text if len(text) >= 40 else ""
    except Exception as exc:  # noqa: BLE001 - 生成失败必须可降级，但日志可见
        logger.warning(f"VIDEO_DESC_LLM_FAILED: {exc}")
        return ""


def ensure_desc(run_root: Path | str, draft: dict) -> str:
    """取该 run 的简介：draft.video_desc → 缓存 description.txt → LLM 生成并落盘 → 清洗兜底。"""
    run_root = Path(run_root)
    vd = str(draft.get("video_desc") or "").strip()
    if vd:
        return humanize(vd) if "/" in vd else vd

    cache = run_root / "description.txt"
    if cache.exists():
        cached = cache.read_text(encoding="utf-8").strip()
        if len(cached) >= 40:
            return cached

    gen = generate_desc(draft)
    if gen:
        try:
            cache.write_text(gen + "\n", encoding="utf-8")
        except OSError as exc:
            logger.warning(f"VIDEO_DESC_CACHE_WRITE_FAILED: {exc}")
        return gen
    return _draft_desc(draft)


def _tags_str(tags) -> str:
    out = []
    for t in (tags or [])[:5]:
        s = str(t).strip()
        if not s:
            continue
        out.append(s if s.startswith("#") else f"#{s}")
    return " ".join(out)


def build_desc(draft: dict, tags=None, platform: str = "channels",
               run_root: Path | str | None = None, include_tags: bool = True) -> str:
    """按平台拼装最终发布文案。

    platform: channels(视频号) | douyin | bilibili
    include_tags: 视频号 provider 内部会自己拼 #标签，此处传 False 避免重复。
    """
    body = ensure_desc(run_root, draft) if run_root else _draft_desc(draft)
    tags = tags if tags is not None else (draft.get("tags") or [])
    tag_str = _tags_str(tags) if include_tags else ""
    title = str(draft.get("title") or "").strip()

    if platform == "douyin":
        head = f"{title} {tag_str}".strip()
        return f"{head}\n{body}".strip()
    # 视频号 / B站：正文在前，标签在后（标题各自走 title 字段）
    return f"{body}\n\n{tag_str}".strip() if tag_str else body.strip()
