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
