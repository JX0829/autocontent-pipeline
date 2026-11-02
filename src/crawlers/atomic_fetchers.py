"""
src/crawlers/atomic_fetchers.py
原子化信源抓取引擎（单一函数只负责单一外部信源）
核心职责：
1. fetch_github_trending: 仅抓取 GitHub Trending 今日飙升榜
2. fetch_hacker_news: 仅抓取 Hacker News 极客与底层架构技术头条
3. fetch_reddit_tech: 仅抓取 Reddit 技术与开发者热门社区探讨
每个函数输入独立、超时独立，输出标准 TrendItem 契约字典。
"""

import os
import sys
import re
import json
import time
import datetime
import subprocess
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional

_TRANS_CACHE = {}

def translate_to_zh(text: str) -> str:
    """全自动中文化翻译与极客语义润色（带在线 API + 内存缓存 + 快速超时熔断）"""
    if not text or not text.strip():
        return ""
    zh_chars = len(re.findall(r'[\u4e00-\u9fa5]', text))
    if zh_chars > len(text) * 0.3:
        return text.strip()

    clean_key = text.strip()
    if clean_key in _TRANS_CACHE:
        return _TRANS_CACHE[clean_key]

    try:
        query_text = clean_key[:300]
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(query_text)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res = "".join([part[0] for part in data[0] if part[0]])
            final_res = res.strip() if res else clean_key
            _TRANS_CACHE[clean_key] = final_res
            return final_res
    except Exception:
        pass

    # 离线极客术语兜底映射
    mapping = {
        "Persistent Context Across Sessions for Every Agent": "专为全自主 Agent 打造的跨会话持久化记忆上下文引擎",
        "The design vocabulary for your coding agent": "为代码智能体量身打造的高级设计与视觉词汇体系",
        "A cross-platform native runtime": "新一代跨平台原生轻量运行时引擎",
        "browser": "轻量无头极速浏览器与自动化内核",
        "Lightweight": "轻量级",
        "framework": "开发框架",
        "engine": "核心引擎",
        "runtime": "运行时",
        "agent": "自主智能体",
        "open source": "开源"
    }
    res = text
    for k, v in mapping.items():
        if k.lower() in res.lower():
            res = re.sub(re.escape(k), v, res, flags=re.IGNORECASE)
    return res.strip()

def verify_link_alive(url: str, timeout: float = 2.0) -> bool:
    """验证链接真实可用性，拦截 404 / 403"""
    if not url or not url.startswith("http"):
        return False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in [200, 301, 302]
    except Exception:
        if "github.com/" in url or "reddit.com/" in url or "ycombinator.com" in url:
            return True
        return False

# ─────────────────────────────────────────────────────────────
# 1. GitHub Trending 原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_github_trending(limit: int = 10, timeout_sec: int = 6) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 GitHub Trending 今日飙升榜
    :param limit: 获取最大条目数
    :param timeout_sec: 超时时间（秒）
    :return: 规范化的 TrendItem 列表
    """
    items: List[Dict[str, Any]] = []
    url = "https://github.com/trending"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            articles = re.findall(r'<article class="Box-row".*?</article>', html, re.DOTALL)
            for a in articles:
                m_repo = re.search(r'href="/([^"/]+/[^"/]+)"', a)
                if not m_repo:
                    continue
                repo = m_repo.group(1).strip()
                if repo.startswith("sponsors") or repo in ["trending"]:
                    continue

                m_desc = re.search(r'<p class="col-9[^"]*">(.*?)</p>', a, re.DOTALL)
                desc = re.sub(r'<[^>]+>', '', m_desc.group(1)).strip() if m_desc else ""

                m_lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', a)
                lang = m_lang.group(1).strip() if m_lang else "Tech"

                m_stars = re.search(r'([0-9,]+)\s+stars\s+today', a)
                stars_today = m_stars.group(1) if m_stars else "0"

                # 强力过滤商业收购/噪音
                if any(w in desc.lower() for w in ["acquire", "acquisition", "lawsuit", "funding", "valuation"]):
                    continue

                zh_desc = translate_to_zh(desc) if desc else "GitHub 今日热门开源项目"
                item_url = f"https://github.com/{repo}"

                items.append({
                    "title": f"开源爆款: {repo}",
                    "desc": f"{zh_desc} (今日暴涨 +{stars_today}⭐)",
                    "url": item_url,
                    "source": "github",
                    "author": repo.split("/")[0],
                    "published_at": "今日实时飙升",
                    "badge": f"GitHub · {lang}",
                    "unique_id": f"gh_{repo.lower()}",
                    "raw": {
                        "repo": repo,
                        "lang": lang,
                        "stars_today": stars_today,
                        "desc_raw": desc
                    }
                })
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"[Warning: GitHub Trending fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 2. Hacker News 原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_hacker_news(limit: int = 10, timeout_sec: int = 5) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 Hacker News 极客与底层架构技术头条
    :param limit: 获取最大条目数
    :param timeout_sec: 超时时间（秒）
    :return: 规范化的 TrendItem 列表
    """
    items: List[Dict[str, Any]] = []
    rss_url = "https://news.ycombinator.com/rss"
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            entries = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
            for entry in entries:
                m_title = re.search(r'<title>(.*?)</title>', entry)
                m_link = re.search(r'<link>(.*?)</link>', entry)
                m_date = re.search(r'<pubDate>(.*?)</pubDate>', entry)
                if not m_title or not m_link:
                    continue

                raw_title = m_title.group(1).strip()
                link = m_link.group(1).strip()
                raw_date = m_date.group(1).strip() if m_date else "实时动态"

                # 格式化日期: Sun, 01 Nov 2026 15:58:28 +0000 -> YYYY-MM-DD HH:MM
                pub_fmt = raw_date
                try:
                    parts = raw_date.split()
                    if len(parts) >= 5:
                        pub_fmt = f"{parts[3]}-{parts[2]}-{parts[1]} {parts[4]}"
                except Exception:
                    pass

                # 强力过滤商业收购/并购/官司等泛商业噪音
                low_title = raw_title.lower()
                if any(kw in low_title for kw in ["acquire", "acquisition", "bought", "valuation", "fundraise", "lawsuit", "antitrust", "ipo", "bankrupt"]):
                    continue

                zh_title = translate_to_zh(raw_title)

                items.append({
                    "title": f"极客头条: {zh_title}",
                    "desc": f"Hacker News 今日全球极客热议技术话题：{zh_title}",
                    "url": link,
                    "source": "hackernews",
                    "author": "YCombinator",
                    "published_at": pub_fmt,
                    "badge": "HackerNews 突破",
                    "unique_id": f"hn_{abs(hash(link)) & 0xffffffff}",
                    "raw": {
                        "raw_title": raw_title,
                        "raw_date": raw_date
                    }
                })
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"[Warning: Hacker News fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 3. Reddit 热门技术话题原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_reddit_tech(
    subreddits: Optional[List[str]] = None,
    limit: int = 10,
    timeout_sec: int = 6
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 Reddit 热门技术社区高热讨论 (r/technology, r/programming, r/SideProject)
    采用官方 Atom/RSS + 归档 API 双轨容错降级，避免 429 报错阻断流程
    :param subreddits: 目标社区列表
    :param limit: 获取最大条目数
    :param timeout_sec: 超时时间（秒）
    :return: 规范化的 TrendItem 列表
    """
    if subreddits is None:
        subreddits = ["technology", "programming", "SideProject"]

    items: List[Dict[str, Any]] = []

    # 优先尝试官方 RSS
    for sub in subreddits:
        if len(items) >= limit:
            break
        rss_url = f"https://www.reddit.com/r/{sub}/.rss"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        try:
            req = urllib.request.Request(rss_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                content = resp.read()
                root = ET.fromstring(content)
                entries = root.findall("{http://www.w3.org/2005/Atom}entry")
                for entry in entries:
                    title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                    link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
                    date_elem = entry.find("{http://www.w3.org/2005/Atom}updated")

                    if title_elem is None or link_elem is None:
                        continue

                    raw_title = (title_elem.text or "").strip()
                    post_url = link_elem.attrib.get("href", "").strip()
                    post_id = re.search(r'/comments/([^/]+)/', post_url)
                    unique_id = f"rd_{post_id.group(1)}" if post_id else f"rd_{abs(hash(post_url)) & 0xffffffff}"

                    # 格式化日期
                    pub_str = "今日实时"
                    if date_elem is not None and date_elem.text:
                        pub_str = date_elem.text[:19].replace("T", " ")

                    zh_title = translate_to_zh(raw_title)

                    items.append({
                        "title": f"社区热议: {zh_title}",
                        "desc": f"Reddit r/{sub} 热门开发者探讨：{zh_title}",
                        "url": post_url,
                        "source": "reddit",
                        "author": f"r/{sub}",
                        "published_at": pub_str,
                        "badge": f"Reddit · {sub}",
                        "unique_id": unique_id,
                        "raw": {
                            "subreddit": sub,
                            "raw_title": raw_title
                        }
                    })
                    if len(items) >= limit:
                        break
        except Exception:
            # 官方 RSS 若触发 429 或限流，平滑降级至开源 Reddit 实时归档 API
            try:
                archive_url = f"https://arctic-shift.photon-reddit.com/api/posts/search?subreddit={sub}&limit=5"
                areq = urllib.request.Request(archive_url, headers={"User-Agent": "curl/7.88.1"})
                with urllib.request.urlopen(areq, timeout=timeout_sec) as aresp:
                    adata = json.loads(aresp.read().decode("utf-8"))
                    for post in adata.get("data", []):
                        raw_title = post.get("title", "")
                        post_id = post.get("id", "")
                        permalink = post.get("permalink", "")
                        post_url = f"https://reddit.com{permalink}" if permalink else post.get("url", "")
                        created_utc = post.get("created_utc", time.time())
                        pub_str = datetime.datetime.fromtimestamp(created_utc).strftime("%Y-%m-%d %H:%M:%S")

                        zh_title = translate_to_zh(raw_title)
                        items.append({
                            "title": f"社区热议: {zh_title}",
                            "desc": f"Reddit r/{sub} 热门探讨：{zh_title}",
                            "url": post_url,
                            "source": "reddit",
                            "author": f"r/{sub}",
                            "published_at": pub_str,
                            "badge": f"Reddit · {sub}",
                            "unique_id": f"rd_{post_id}" if post_id else f"rd_{abs(hash(post_url)) & 0xffffffff}",
                            "raw": post
                        })
                        if len(items) >= limit:
                            break
            except Exception as e2:
                print(f"[Warning: Reddit fallback API failed for r/{sub}]: {e2}", file=sys.stderr)

    return items[:limit]

# ─────────────────────────────────────────────────────────────
# 4. Hugging Face Daily Papers 原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_huggingface_daily_papers(limit: int = 5, timeout_sec: int = 5) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 Hugging Face Daily Papers 今日高赞前沿 AI 论文
    """
    items: List[Dict[str, Any]] = []
    url = "https://huggingface.co/api/daily_papers"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for entry in data[:limit]:
                paper = entry.get("paper", {})
                title = paper.get("title", "")
                paper_id = paper.get("id", "")
                summary = paper.get("summary", "")
                upvotes = entry.get("numComments", 0) + paper.get("upvotes", 0)
                published_at = entry.get("publishedAt", "今日前沿")[:10]

                if not title:
                    continue

                zh_title = translate_to_zh(title)
                zh_desc = translate_to_zh(summary[:120].replace("\n", " "))
                paper_url = f"https://huggingface.co/papers/{paper_id}"

                items.append({
                    "title": f"顶会论文: {zh_title}",
                    "desc": f"{zh_desc} (全球点赞 +{upvotes}⭐)",
                    "url": paper_url,
                    "source": "huggingface",
                    "author": "HuggingFace",
                    "published_at": published_at,
                    "badge": "HF · 论文",
                    "unique_id": f"hf_{paper_id}",
                    "raw": entry
                })
    except Exception as e:
        print(f"[Warning: HuggingFace fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 5. arXiv AI / ML 预印本原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_arxiv_ai_papers(
    categories: Optional[List[str]] = None,
    limit: int = 5,
    timeout_sec: int = 5
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 arXiv 最新提交的计算机视觉、大模型与自然语言预印本 (cs.AI, cs.CL)
    """
    if categories is None:
        categories = ["cs.AI", "cs.CL"]
    items: List[Dict[str, Any]] = []
    cat_query = "+OR+".join([f"cat:{c}" for c in categories])
    api_url = f"https://export.arxiv.org/api/query?search_query={cat_query}&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            content = resp.read().decode("utf-8")
            root = ET.fromstring(content)
            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            for entry in entries:
                t_elem = entry.find("{http://www.w3.org/2005/Atom}title")
                id_elem = entry.find("{http://www.w3.org/2005/Atom}id")
                s_elem = entry.find("{http://www.w3.org/2005/Atom}summary")
                pub_elem = entry.find("{http://www.w3.org/2005/Atom}published")

                if t_elem is None or id_elem is None:
                    continue

                raw_title = re.sub(r'\s+', ' ', t_elem.text or "").strip()
                raw_id = (id_elem.text or "").strip()
                raw_summary = re.sub(r'\s+', ' ', s_elem.text or "").strip() if s_elem is not None else ""
                pub_date = (pub_elem.text or "今日提交")[:10] if pub_elem is not None else "今日提交"

                zh_title = translate_to_zh(raw_title)
                zh_desc = translate_to_zh(raw_summary[:120])
                clean_id = raw_id.split("/")[-1]

                items.append({
                    "title": f"学术预印: {zh_title}",
                    "desc": f"arXiv 前沿算法突破：{zh_desc}",
                    "url": raw_id,
                    "source": "arxiv",
                    "author": "arXiv",
                    "published_at": pub_date,
                    "badge": "arXiv · 学术",
                    "unique_id": f"arxiv_{clean_id}",
                    "raw": {"title": raw_title, "summary": raw_summary}
                })
    except Exception as e:
        print(f"[Warning: arXiv fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 6. Techmeme 硅谷产业与前沿技术突发原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_techmeme_breaking(limit: int = 5, timeout_sec: int = 5) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：只负责抓取 Techmeme 硅谷重大技术发布与产业突发 (100% 过滤商业八卦/并购)
    """
    items: List[Dict[str, Any]] = []
    rss_url = "https://www.techmeme.com/feed.xml"
    req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})

    EXCLUDE_KEYWORDS = [
        "acquire", "acquisition", "stake in", "buys", "bought", "buying",
        "livestream", "subscriber", "lawsuit", "sued", "suing", "antitrust",
        "valuation", "fundraise", "ipo", "earnings", "revenue", "billion", "million",
        "pr blitz", "policy", "magazine", "profile of", "director", "insuring against",
        "debt default", "freecash", "vc-backed", "binance", "bitcoin", "crypto"
    ]
    TECH_KEYWORDS = [
        "ai model", "foundation model", "deepseek", "claude", "gpt-", "llama", "qwen",
        "agent", "agents", "multi-agent", "open-source", "open source", "weights",
        "framework", "gpu", "cuda", "inference", "quantization", "gguf", "benchmark",
        "swe-bench", "compiler", "runtime", "rust", "python", "developer tool", "chip"
    ]

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            entries = re.findall(r'<item>(.*?)</item>', content, re.DOTALL)
            for entry in entries:
                m_title = re.search(r'<title>(.*?)</title>', entry)
                m_link = re.search(r'<link>(.*?)</link>', entry)
                m_date = re.search(r'<pubDate>(.*?)</pubDate>', entry)
                if not m_title or not m_link:
                    continue
                raw_title = m_title.group(1).strip()
                link = m_link.group(1).strip()
                clean_title = re.sub(r'\s*\([^)]+/[^)]+\)$', '', raw_title)
                clean_title = clean_title.replace("&apos;", "'").replace("&quot;", '"').replace("&amp;", "&")
                title_lower = clean_title.lower()

                if any(k in title_lower for k in EXCLUDE_KEYWORDS):
                    continue
                if not any(k in title_lower for k in TECH_KEYWORDS):
                    continue

                zh_title = translate_to_zh(clean_title)
                m_desc = re.search(r'<description><!\[CDATA\[(.*?)\]\]></description>', entry, re.DOTALL)
                desc_text = re.sub(r'<[^>]+>', '', m_desc.group(1)).strip() if m_desc else ""
                zh_desc = translate_to_zh(desc_text[:120]) if desc_text else "全球权威科技突破与工程架构深度进展。"
                pub_fmt = m_date.group(1).strip()[:16] if m_date else "今日突发"

                items.append({
                    "title": f"产业突发: {zh_title}",
                    "desc": zh_desc,
                    "url": link,
                    "source": "techmeme",
                    "author": "Techmeme",
                    "published_at": pub_fmt,
                    "badge": "Techmeme · 硬核",
                    "unique_id": f"tm_{abs(hash(link)) & 0xffffffff}",
                    "raw": {"raw_title": raw_title}
                })
                if len(items) >= limit:
                    break
    except Exception as e:
        print(f"[Warning: Techmeme fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 7. Reddit 垂直 AI 极客实操原子抓取函数 (r/LocalLLaMA)
# ─────────────────────────────────────────────────────────────

def fetch_reddit_ai_hot(
    subreddits: Optional[List[str]] = None,
    limit: int = 5,
    timeout_sec: int = 5
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：专攻全球顶级开源大模型与端侧推理社区 (r/LocalLLaMA, r/MachineLearning)
    """
    if subreddits is None:
        subreddits = ["LocalLLaMA", "MachineLearning"]
    return fetch_reddit_tech(subreddits=subreddits, limit=limit, timeout_sec=timeout_sec)

# ─────────────────────────────────────────────────────────────
# 8. GitHub AI 垂直高星黑马项目原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_github_ai_topics(
    topic: str = "llm",
    limit: int = 5,
    timeout_sec: int = 5
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：专攻 GitHub 垂直 AI 与 Agent 领域最新高星爆发项目
    """
    items: List[Dict[str, Any]] = []
    url = f"https://api.github.com/search/repositories?q=topic:{topic}+stars:>50&sort=updated&order=desc&per_page={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for repo_data in data.get("items", [])[:limit]:
                full_name = repo_data.get("full_name", "")
                desc = repo_data.get("description", "") or "GitHub 垂直 AI 开源突破"
                stars = repo_data.get("stargazers_count", 0)
                html_url = repo_data.get("html_url", "")
                pushed_at = repo_data.get("pushed_at", "今日更新")[:10]
                lang = repo_data.get("language") or "Python"

                zh_desc = translate_to_zh(desc)
                items.append({
                    "title": f"AI代码库: {full_name}",
                    "desc": f"{zh_desc} (累计获星 {stars}⭐)",
                    "url": html_url,
                    "source": "github_topic",
                    "author": full_name.split("/")[0],
                    "published_at": pushed_at,
                    "badge": f"AI库 · {lang}",
                    "unique_id": f"ght_{full_name.lower().replace('/', '_')}",
                    "raw": {"full_name": full_name, "stars": stars}
                })
    except Exception as e:
        print(f"[Warning: GitHub Topic fetch failed]: {e}", file=sys.stderr)
    return items

# ─────────────────────────────────────────────────────────────
# 9. 小红书极客与开发者垂直玩法原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_xiaohongshu_tech(
    query: str = "AI智能体 OR 大模型架构 OR 开源AI",
    limit: int = 5,
    timeout_sec: int = 10
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：基于 agent-reach opencli 检索小红书前沿技术与开发玩法笔记
    """
    items: List[Dict[str, Any]] = []
    agent_reach_bin = "/Users/a1/.local/bin/agent-reach"
    if not os.path.exists(agent_reach_bin):
        return items

    cmd = [agent_reach_bin, "xiaohongshu", "search", query, "--limit", str(limit)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            for it in data.get("items", []):
                title = it.get("title") or "小红书开发者前沿笔记"
                url = it.get("url") or ""
                author = it.get("author") or "小红书极客"
                pub_at = it.get("published_at") or "今日热点"
                raw = it.get("raw") or {}
                
                # 提取唯一的 note_id 作为去重指纹
                note_id = ""
                if "search_result/" in url:
                    note_id = url.split("search_result/")[1].split("?")[0]
                elif "discovery/item/" in url:
                    note_id = url.split("discovery/item/")[1].split("?")[0]
                else:
                    note_id = hashlib.md5(url.encode()).hexdigest()[:12]

                items.append({
                    "title": f"前沿笔记: {title}",
                    "desc": f"小红书创作者 @{author} 发布的最新 AI 实践与开发者技术探讨",
                    "url": url,
                    "source": "xiaohongshu",
                    "author": author,
                    "published_at": pub_at,
                    "badge": "小红书 · 极客",
                    "unique_id": f"xhs_{note_id}",
                    "raw": raw
                })
    except Exception as e:
        print(f"[Warning: Xiaohongshu fetch failed]: {e}", file=sys.stderr)
    return items[:limit]

# ─────────────────────────────────────────────────────────────
# 10. Twitter (X) 全球 AI 领袖一手动态原子抓取函数
# ─────────────────────────────────────────────────────────────

def fetch_twitter_x_tech(
    query: str = "Claude 3.7 OR DeepSeek OR Coding Agent OR OpenAI",
    limit: int = 5,
    timeout_sec: int = 15
) -> List[Dict[str, Any]]:
    """
    单一信源原子函数：基于 agent-reach opencli 检索全球 X (Twitter) 先锋一手推文
    """
    items: List[Dict[str, Any]] = []
    agent_reach_bin = "/Users/a1/.local/bin/agent-reach"
    if not os.path.exists(agent_reach_bin):
        return items

    cmd = [agent_reach_bin, "twitter", "search", query, "--limit", str(limit)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if res.returncode == 0 and res.stdout.strip():
            data = json.loads(res.stdout)
            for it in data.get("items", []):
                text_content = it.get("text") or it.get("title") or ""
                url = it.get("url") or ""
                author = it.get("author") or "AI Researcher"
                pub_at = it.get("published_at") or "实时动态"
                
                # 简单清洗推文正文作为摘要与标题
                clean_lines = [l.strip() for l in text_content.splitlines() if l.strip()]
                first_line = clean_lines[0] if clean_lines else "全球 AI 前沿动态突破"
                if len(first_line) > 35:
                    first_line = first_line[:35] + "..."
                
                # 提取 tweet_id
                tweet_id = url.split("status/")[-1].split("?")[0] if "status/" in url else hashlib.md5(text_content.encode()).hexdigest()[:12]
                zh_desc = translate_to_zh(text_content[:200])

                items.append({
                    "title": f"推特先锋: @{author}: {first_line}",
                    "desc": zh_desc or text_content[:150],
                    "url": url,
                    "source": "twitter",
                    "author": author,
                    "published_at": pub_at[:16] if len(pub_at) >= 16 else pub_at,
                    "badge": "X · 先锋推文",
                    "unique_id": f"tw_{tweet_id}",
                    "raw": it
                })
    except Exception as e:
        print(f"[Warning: Twitter fetch failed]: {e}", file=sys.stderr)
    return items[:limit]


