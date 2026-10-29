import os
import sys
import json
import logging
import sqlite3
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from src.schemas import DraftScriptPayload

logger = logging.getLogger("HotspotExtractor")

SYSTEM_PROMPT = """你是一名资深科技短视频架构师与极客爆款创作者。
你的任务是将用户提供的最新科技热点新闻列表进行评估，严格挑选出【最具爆点、最能引发极客与开发者群体讨论/收藏、最具备生产力冲击】的最新科技热点，并转化为符合渲染标准的 DraftScriptPayload JSON。

【爆款选题评估法则（v1.0.1 核心准则）】：
1. 优先打高分：
   - 行业巨头撕扯与生态掀桌（如 OpenAI、Anthropic、JetBrains、微软的降维打击事件）。
   - 昂贵商业软件的“免费全本地平替”（如 Cursor、ElevenLabs、Midjourney 的开源自建方案）。
   - 价格/成本暴跌与性能颠覆（如降价75%、单卡跑大模型、算力/推理提速翻倍）。
   - 程序员阵营话题（如编程语言更迭、生态替代）。
2. 坚决降权与改写：
   - 纯学术预印本/数学证明/云服务B端合规基建/抽象协议直接过滤。
   - 若遇到硬核模型或技术，文案严禁书面学术化堆砌（杜绝'投机采样'、'级联Block'、'企业级治理'），必须翻译为受众直接收益（如'老显卡狂喜'、'普通电脑断网秒跑'、'告别万元账单'）。
3. 动态视觉模板适配（拒绝千篇一律）：
   - 生产力工具/Web应用/IDE：推荐 template_id='notion_craft' 或 'minimal_editorial'
   - 底层大模型/终端代码/黑客工具：推荐 template_id='linear_obsidian' 或 'geek_terminal_v3'
   - 突破性行业重磅新闻：推荐 template_id='retro_terminal' 或 'cyber_blueprint'
4. 结尾探讨引导（严禁外部链接与导流）：
   - 严禁出现完整URL、域名、'暗号'、'私信'、'主页置顶'或'在GitHub搜索'等诱导去站外搜索的词汇。
   - 尾镜一律收敛为开放式技术探讨与生动比喻（例如杯型划分、性价比对比），激发评论区自发讨论。

生成的 DraftScriptPayload 必须遵循以下规范：
1. theme: 必须是 "tech-github" 或 "ai-product" 或 "breaking-news"
2. entity_key: 唯一事件或项目标识，如 github:owner/repo 或 news:event-slug
3. title: 抖音/小红书吸引眼球的极客标题（不超过28字）
4. cover_main_text: 封面正中央超大主标题（简短有力，8~12字，九宫格不裁切）
5. cover_sub_text: 封面副标题标签（如 "开源全球首发" / "突破性进展"，6~14字）
6. hook_narration: 黄金前3秒抓人吸睛口播（严禁超过30字！必须极其简练抓人，不超过30字）
7. fact_summary_150: 供评论区智能防护沙箱使用的客观核心事实摘要（不超过150字）
8. resource_keyword: 选题英文标识或项目简称（如 gpuunlock, llmd, markitdown 等，用于任务标识与目录命名，不超过10字）
9. scenes: 严格包含 3 到 4 个分镜场景（每个分镜包含 scene_index, segment_narration, visual_type, highlight_text, sfx_cue）：
   - scene_index: 0, 1, 2, 3
   - segment_narration: 该分镜的中文旁白（自然流畅，单句不要过长）
   - visual_type: 必须是 "terminal_code"（终端代码打字机）、"ui_card"（特性卡片）、"stat_chart"（指标数据卡片）、"media_showcase"（媒体展示）之一
   - code_snippet: 当 visual_type 为 terminal_code 时提供真实的代码/命令行调用（其余类型设为 null）。必须严格遵守外部导流防护规则与合规底线：
     * 【绝对红线·严禁外部链接与导流】：代码片段、终端命令、分镜旁白（segment_narration）、口播（hook_narration）及所有文字中【严禁出现任何完整 URL、域名或外链】（如包含 http://, https://, github.com 等完整链接）。小红书与抖音 OCR 会检测视频画面中的外链导致严重限流！展示克隆或安装命令时，只能写项目名、包名或搜索关键词，例如必须写 `pip install deepinfer` 或 `gh repo clone deepseek-ai/DeepInfer`（不带 https://github.com），或者直接写 `cd deepinfer && pip install -e .`。
     * 【禁止导流暗号话术】：旁白与文案中【严禁使用“暗号”、“私信”、“主页”、“粉丝群”等诱导互动词汇】（平台会直接判定违规限流/封禁），如果需要提及资源获取，统一使用“在 GitHub 搜索【项目名】查看”或“搜索【关键词】查看”。
     * 【敏感拦截词过滤】：严禁在代码或台词中出现任何敏感拦截词汇或变体，包括：'api_key'、'apikey'、'sk-'、'token'、'password'、'prompt injection'、'prompt注入'、'越狱'、'jailbreak'、'system prompt'、'系统提示' 等（哪怕在注释或模拟输出中也会100%触发本地DFA安全检查报错中断！），展示客户端初始化必须写 `client = OpenAI()`，安全检测可使用“恶意指令”、“指令投毒”、“安全漏洞”代替！
   - stat_items: 当 visual_type 为 ui_card 或 stat_chart 时提供 2~3 个结构化指标 [{"label": "...", "value": "..."}]（其余类型设为 null）
   - highlight_text: 分镜顶部醒目的短标题徽章（不超过16字）
   - sfx_cue: 关键帧音效，必须是 "typing" / "whoosh" / "ding" / "glitch" 之一
10. tags: 4~6 个精准科技话题标签列表
11. target_account_type: "tech"

你必须只输出纯 JSON 字符串，严禁包含任何 Markdown 格式标记（如 ```json ）。"""

def get_recent_pipeline_topics(limit: int = 50) -> list:
    """从本地数据库获取最近已处理/已生成的短视频主题"""
    db_path = PROJECT_ROOT / "data" / "pipeline_state.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT task_id, entity_key, draft_script_json, created_at FROM content_tasks ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        topics = []
        for tid, ek, dj, cat in rows:
            title = ""
            slug = ""
            if dj:
                try:
                    d = json.loads(dj)
                    title = d.get("title", "")
                    slug = d.get("resource_keyword", "")
                except Exception:
                    pass
            topics.append({"task_id": tid, "entity_key": ek or "", "title": title, "slug": slug or ""})
        return topics
    except Exception as e:
        logger.warning(f"Failed to read pipeline db: {e}")
        return []

def check_topic_duplicate(candidate: dict, recent_topics: list) -> tuple:
    """代码级严格硬去重判断"""
    cand_title = (candidate.get("title") or "").lower()
    cand_slug = (candidate.get("resource_keyword") or "").lower()
    cand_key = (candidate.get("entity_key") or "").lower()
    cand_norm_title = "".join(c for c in cand_title if c.isalnum() or '\u4e00' <= c <= '\u9fff')

    for rec in recent_topics:
        rec_title = (rec.get("title") or "").lower()
        rec_slug = (rec.get("slug") or "").lower()
        rec_key = (rec.get("entity_key") or "").lower()
        rec_norm_title = "".join(c for c in rec_title if c.isalnum() or '\u4e00' <= c <= '\u9fff')

        # 1. 关键词 slug 精准比对
        if cand_slug and rec_slug and cand_slug == rec_slug and len(cand_slug) >= 3:
            return True, f"选题 slug '{cand_slug}' 与已有任务 ({rec.get('task_id')}) 重复"

        # 2. entity_key 比对
        if cand_key and rec_key and cand_key == rec_key:
            return True, f"事件标识 entity_key '{cand_key}' 重复"

        # 3. 核心科技实体名称硬比对
        for keyword in ["hbm4", "hbm", "genie", "thinkrail", "ginux", "openbot", "hallmark", "deeptutor", "cindy", "survival llm", "deepinfer", "kimik3", "pydantic"]:
            if keyword in cand_title and keyword in rec_title:
                return True, f"核心实体 '{keyword}' 近期已制作过短视频 ({rec_title})"
            if keyword in cand_slug and keyword in rec_slug:
                return True, f"核心实体 slug '{keyword}' 近期已制作过短视频"

        # 4. 标题子串高度重叠
        if cand_norm_title and rec_norm_title:
            if cand_norm_title in rec_norm_title or rec_norm_title in cand_norm_title:
                return True, f"标题与已有视频高度重叠 ({rec_title})"

    return False, ""

def pick_and_convert_hotspot(news_text: str, task_id: str = None) -> dict:
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = os.getenv("LLM_MODEL_MAIN", "deepseek-chat")

    if not api_key:
        raise ValueError("LLM_API_KEY 未在环境变量中配置")

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 1. 查询近期已制作的排他黑名单
    recent_topics = get_recent_pipeline_topics(limit=30)
    exclude_items = []
    for rt in recent_topics:
        t_desc = rt.get("title") or rt.get("slug") or rt.get("entity_key")
        if t_desc:
            exclude_items.append(t_desc)

    exclude_prompt = ""
    if exclude_items:
        unique_excludes = list(dict.fromkeys(exclude_items))[:20]
        exclude_prompt = "\n\n【🚨 选题排他黑名单（以下主题在近期已制作过短视频，严禁再次挑选重复话题！必须挑选列表中全新的增量热点）：】\n" + "\n".join([f"- {t}" for t in unique_excludes])

    prompt = f"以下是本次全网监控提炼出的最新热点资讯列表：\n\n{news_text}{exclude_prompt}\n\n请严格挑选出其中最重磅的【一条最新且未制作过的科技热点】，提炼并生成标准的 DraftScriptPayload JSON。"

    logger.info("Calling LLM to pick top tech hotspot and generate script payload...")
    
    max_retries = 3
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    for attempt in range(max_retries):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3 + (attempt * 0.2),
            response_format={"type": "json_object"}
        )

        raw_json = response.choices[0].message.content.strip()
        data = json.loads(raw_json)

        # 校验合法性
        payload = DraftScriptPayload(**data)
        result = payload.model_dump()

        # 代码级严格去重校验
        is_dup, reason = check_topic_duplicate(result, recent_topics)
        if is_dup:
            logger.warning(f"[Attempt {attempt+1}] Picked topic is duplicate: {reason}. Retrying next candidate...")
            if attempt < max_retries - 1:
                messages.append({"role": "assistant", "content": raw_json})
                messages.append({
                    "role": "user", 
                    "content": f"你挑选的选题 '{result.get('title')}' 命中了历史已制作黑名单（原因: {reason}）！请绝对排除它，重新挑选资讯列表中【未被制作过的第二候选热点】！"
                })
                continue
            else:
                logger.error(f"All {max_retries} attempts picked duplicate topics! Fallback to last.")

        if task_id:
            result["task_id"] = task_id
        else:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            slug = result.get("resource_keyword", "hotspot").lower()
            result["task_id"] = f"news_{slug}_{date_str}"

        return result

