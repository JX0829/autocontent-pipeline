import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from src.schemas import DraftScriptPayload

logger = logging.getLogger("HotspotExtractor")

SYSTEM_PROMPT = """你是一名资深科技短视频架构师与极客创作者。
你的任务是将用户提供的最新科技热点新闻列表进行评估，严格挑选出【最具爆点、技术硬核度最高、最值得做成极客短视频】的一条最新科技热点，并将其转化为符合短视频渲染标准的 DraftScriptPayload JSON。

生成的 DraftScriptPayload 必须遵循以下规范：
1. theme: 必须是 "tech-github" 或 "ai-product" 或 "breaking-news"
2. entity_key: 唯一事件或项目标识，如 github:owner/repo 或 news:event-slug
3. title: 抖音/小红书吸引眼球的极客标题（不超过28字）
4. cover_main_text: 封面正中央超大主标题（简短有力，8~12字，九宫格不裁切）
5. cover_sub_text: 封面副标题标签（如 "开源全球首发" / "突破性进展"，6~14字）
6. hook_narration: 黄金前3秒抓人吸睛口播（不超过35字）
7. fact_summary_150: 供评论区智能防护沙箱使用的客观核心事实摘要（不超过150字）
8. resource_keyword: 选题英文标识或项目简称（如 gpuunlock, llmd, markitdown 等，用于任务标识与目录命名，不超过10字）
9. scenes: 严格包含 3 到 4 个分镜场景（每个分镜包含 scene_index, segment_narration, visual_type, highlight_text, sfx_cue）：
   - scene_index: 0, 1, 2, 3
   - segment_narration: 该分镜的中文旁白（自然流畅，单句不要过长）
   - visual_type: 必须是 "terminal_code"（终端代码打字机）、"ui_card"（特性卡片）、"stat_chart"（指标数据卡片）、"media_showcase"（媒体展示）之一
   - code_snippet: 当 visual_type 为 terminal_code 时提供真实的代码/命令行调用（其余类型设为 null）。必须注意：严禁在代码中出现任何形如 'api_key'、'apikey'、'sk-'、'token'、'password' 等词汇或其变体（会100%触发本地安全规则报错中断），展示客户端初始化必须写 `client = OpenAI()` 或省略授权参数！
   - stat_items: 当 visual_type 为 ui_card 或 stat_chart 时提供 2~3 个结构化指标 [{"label": "...", "value": "..."}]（其余类型设为 null）
   - highlight_text: 分镜顶部醒目的短标题徽章（不超过16字）
   - sfx_cue: 关键帧音效，必须是 "typing" / "whoosh" / "ding" / "glitch" 之一
10. tags: 4~6 个精准科技话题标签列表
11. target_account_type: "tech"

你必须只输出纯 JSON 字符串，严禁包含任何 Markdown 格式标记（如 ```json ）。"""

def pick_and_convert_hotspot(news_text: str, task_id: str = None) -> dict:
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    model = os.getenv("LLM_MODEL_MAIN", "deepseek-chat")

    if not api_key:
        raise ValueError("LLM_API_KEY 未在环境变量中配置")

    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt = f"以下是本次2小时全网监控提炼出的最新热点资讯列表：\n\n{news_text}\n\n请严格挑选出其中最重磅的【一条最新科技热点】，提炼并生成标准的 DraftScriptPayload JSON。"

    logger.info("Calling LLM to pick top tech hotspot and generate script payload...")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3,
        response_format={"type": "json_object"}
    )

    raw_json = response.choices[0].message.content.strip()
    data = json.loads(raw_json)

    # 校验合法性
    payload = DraftScriptPayload(**data)
    result = payload.model_dump()
    if task_id:
        result["task_id"] = task_id
    else:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = result.get("resource_keyword", "hotspot").lower()
        result["task_id"] = f"news_{slug}_{date_str}"

    return result
