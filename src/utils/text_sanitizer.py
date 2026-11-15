import re

def ensure_clean_short_title(title: str, max_chars: int = 30) -> str:
    """
    智能重拟标题，确保 <= max_chars（默认 30），严禁物理机械截断（[:30] 或带省略号）：
    若超出 30 字，则提炼出意思相同、更短且表意完整的精炼标题。
    """
    if not title:
        return ""
    title = title.strip()
    if len(title) <= max_chars:
        return title

    # 1. 清理平台前缀
    clean_t = re.sub(r'^(推特先锋|社区热议|最新速报|突发爆料|开源爆款)[：:\s]*', '', title)
    clean_t = re.sub(r'[\s\.\!\?。！？…]+$', '', clean_t)
    if len(clean_t) <= max_chars:
        return clean_t

    # 2. 剥离媒体后缀与括号补充
    clean_t = re.sub(r'\s*[-—]\s*[A-Za-z0-9_]+$', '', clean_t)
    clean_t = re.sub(r'（[^）]+）|\([^\)]+\)', '', clean_t)
    if len(clean_t) <= max_chars and len(clean_t) >= 8:
        return clean_t

    # 3. 常见长词替换压缩
    replacements = [
        ("建议零售价为", "定价"),
        ("建议零售价", "定价"),
        ("官方建议售价", "定价"),
        ("人工智能", "AI"),
        ("大语言模型", "大模型"),
        ("的重大更新", "重大更新"),
        ("背后的训练流水线", "训练全流程"),
        ("背后的训练流程", "训练全流程"),
        ("彻底炸锅了", "全网炸锅"),
        ("彻底刷屏了", "全网刷屏"),
        ("彻底公开了", "正式公开"),
        ("神级开源导航", "开源神级导航"),
        ("大模型套壳软件这次真天塌了", "套壳软件天塌了"),
        ("一台 RTX 3090 上的 DeepSeek-V4.1-Flash-1017", "单卡3090跑DeepSeek"),
        ("NVIDIA RTX PRO 5500 Blackwell", "英伟达RTX PRO 5500"),
    ]
    for old_w, new_w in replacements:
        clean_t = clean_t.replace(old_w, new_w)
        if len(clean_t) <= max_chars:
            return clean_t

    # 4. 提取主从句
    parts = re.split(r'[！!？?。；;：:\s]+', clean_t)
    parts = [p.strip() for p in parts if p.strip()]
    for p in parts:
        if 10 <= len(p) <= max_chars:
            return p

    if len(parts) >= 2:
        cand = f"{parts[0]}：{parts[1]}"
        if len(cand) <= max_chars:
            return cand

    # 5. 优雅边界收敛，去除悬空助词
    res = clean_t[:max_chars].rstrip("的得地与和及在对以由为从上中下个，,：: ")
    return res


# ── 短标题派生（2026-11-15）：只在语义边界切，绝不半截词、绝不加省略号 ──
_BOUNDARY_PUNCT = "，。；：！？、,;:!?｜|·—～~"
_LATIN_TAIL = re.compile(r"[A-Za-z0-9\-_/.]+$")


def derive_short_title(text: str, limit: int = 16) -> str:
    """从描述首句派生 ≤limit 字的短标题，语义完整。

    规则（依次尝试，命中即返回）：
      1. 原文不超限 → 直接返回（去掉尾部标点/空白）；
      2. 按标点切成短语，取第一个长度在 [4, limit] 的短语；
      3. 硬切到 limit，然后**剥掉被切断的英文/数字残词**（`Auto-Onto` 这种半截），
         再去掉尾部标点；剥完不足 4 字则退一级；
      4. 都不行 → 返回空串（调用方记 SHORT_TITLE_UNRESOLVED，不许塞半截词）。
    """
    t = (text or "").strip()
    if not t:
        return ""

    def _tidy(s: str) -> str:
        s = s.strip().strip(_BOUNDARY_PUNCT).strip()
        return s

    if len(t) <= limit:
        return _tidy(t)

    # 2. 标点边界
    parts = re.split(f"[{re.escape(_BOUNDARY_PUNCT)}]", t)
    for seg in parts:
        seg = _tidy(seg)
        if 4 <= len(seg) <= limit:
            return seg

    # 3. 硬切 + 剥残词
    cut = t[:limit]
    for _ in range(3):
        s = _tidy(cut)
        if _LATIN_TAIL.search(s):
            s = _tidy(_LATIN_TAIL.sub("", s))
        if len(s) >= 4:
            return s
        cut = cut[:-1]
    return ""


def pick_short_title(text: str, limit: int = 16, override: str = "") -> str:
    """短标题最终取值：人工覆盖 > 清洗器结果（若未半截词）> 语义边界派生。

    判「半截词」：英文/数字 token 被切断（`Auto-Onto` / `r/LocalL`），
    或以「的/和/与/在/把/被/让/给/对/为」这类虚词收尾 —— 都算语义不完整。
    """
    if override:
        return override.strip()[:limit]
    head = (text or "").strip().split("\n")[0].strip()
    if not head:
        return ""
    cand = ensure_clean_short_title(head, limit)
    bad_tail = (bool(_LATIN_TAIL.search(cand))
                or cand.endswith(("的", "和", "与", "在", "把", "被", "让", "给", "对", "为", "了"))
                or any(ch in cand for ch in "，；：、,;"))
    unbalanced = cand.count("《") != cand.count("》") or cand.count("（") != cand.count("）")
    if 4 <= len(cand) <= limit and not bad_tail and not unbalanced:
        return cand
    d = derive_short_title(head, limit)
    # 派生结果太短、而首句里有完整英文/型号 token → 用 token 更有信息量
    if d and len(d) <= limit * 0.6:
        tok = max((t for t in re.findall(r"[A-Za-z][A-Za-z0-9\-/\.]{3,}", head) if len(t) <= limit),
                  key=len, default="")
        if tok:
            return tok
    return d or cand
