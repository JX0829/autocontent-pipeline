import asyncio
import os
import sys
import json
import subprocess
from datetime import datetime
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.insert(0, PROJECT_ROOT)

from src.db import PipelineDB
from src.schemas import DraftScriptPayload
from src.safety.dfa_filter import DFAFilter
from src.tts.aligner import TTSAligner

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

NEWS_TASKS = [
    {
        "task_id": "news_hf_dna_001",
        "theme": "ai-product",
        "entity_key": "huggingface:dna-llm-biology",
        "title": "HuggingFace开源生物模型！像聊天一样读懂人类DNA",
        "cover_main_text": "像聊天一样读DNA",
        "cover_sub_text": "HuggingFace 开源",
        "hook_narration": "Hugging Face刚刚开源了全新生物大模型，人类基因组终于能对话了！",
        "fact_summary_150": "Hugging Face发布开源生物AI模型，支持以对话式交互直接解析DNA序列。研究人员无需编写复杂代码即可探索基因变异对活性及健康的影响，大幅降低计算生物学门槛。",
        "resource_keyword": "DNA-AI",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "Hugging Face刚刚开源了全新生物大模型，人类基因组终于能对话了！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "交互模式", "value": "自然语言对话解析"},
                    {"label": "技术门槛", "value": "无需编写复杂算法"},
                    {"label": "开源生态", "value": "Hugging Face 全球首发"}
                ],
                "highlight_text": "颠覆计算生物学",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "过去需要专业生信算法的基因分析，现在直接在网页对话框就能实时探索突变影响。",
                "visual_type": "terminal_code",
                "code_snippet": "from transformers import AutoModelForDNA\n\nmodel = AutoModelForDNA.from_pretrained('hf/dna-model')\nres = model.chat('请分析该基因序列的致病变异风险: ATCGGA...')\nprint(res.insights)",
                "highlight_text": "对话式探索生命密码",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 2,
                "segment_narration": "它能直观预测基因变异对疾病的影响，生物医药研发效率直接提升几十倍！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "分析效率提升", "value": "+100x ⚡"},
                    {"label": "应用场景", "value": "靶向药 / 罕见病筛查"},
                    {"label": "计算成本", "value": "大幅降低 90%"}
                ],
                "highlight_text": "AI医药研发大提速",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 3,
                "segment_narration": "生命科学彻底迎来属于自己的GPT时刻，赶紧点赞收藏，回复暗号获取开源地址！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "开源协议", "value": "Apache-2.0 完全商用"},
                    {"label": "获取方式", "value": "主页置顶自取源码"}
                ],
                "highlight_text": "AI重塑生命科学",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["HuggingFace", "AI生物", "生命科学", "Python", "开源模型"],
        "target_account_type": "tech"
    },
    {
        "task_id": "news_deepseek_funding_002",
        "theme": "breaking-news",
        "entity_key": "news:deepseek-15b-round",
        "title": "突发！DeepSeek斩获千亿融资，全球大模型格局彻底变天！",
        "cover_main_text": "DeepSeek千亿融资",
        "cover_sub_text": "国产大模型突发",
        "hook_narration": "突发核弹级消息！中国大模型领头羊DeepSeek刚刚完成千亿级巨额融资！",
        "fact_summary_150": "中国头部大模型公司DeepSeek完成新一轮约150亿美元（1000亿元人民币）融资，资金将用于下一代前沿基座大模型研发与超大规模自研算力集群搭建，正面挑战OpenAI。",
        "resource_keyword": "DeepSeek",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "突发核弹级消息！中国大模型领头羊DeepSeek刚刚完成千亿级巨额融资！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "融资金额", "value": "约 1000 亿人民币 ($15B)"},
                    {"label": "全球行业地位", "value": "中国AI超级独角兽"},
                    {"label": "核心战力", "value": "MoE 超低成本架构"}
                ],
                "highlight_text": "千亿级超级重注",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "这笔重磅弹药将全量砸向下一代万亿参数基座模型，以及超大规模自建算力集群！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "算力规划", "value": "万卡级自建算力中心"},
                    {"label": "对标目标", "value": "OpenAI / Anthropic"},
                    {"label": "研发路线", "value": "下一代超级推理模型"}
                ],
                "highlight_text": "万卡算力正面硬刚",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 2,
                "segment_narration": "在极致性价比和开源推理上彻底打痛硅谷后，国产AI终于拥有了正面对决的超强资本！",
                "visual_type": "terminal_code",
                "code_snippet": "# 硅谷公认的最强开源推理模型\ncurl https://api.deepseek.com/v1/chat/completions \\\n  -H 'Authorization: Bearer ***' \\\n  -d '{\"model\": \"deepseek-reasoner\", \"stream\": true}'",
                "highlight_text": "颠覆硅谷算力垄断",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 3,
                "segment_narration": "顶尖算力加上顶尖工程算法，这场全球AI终局对决，你更看好谁？评论区聊聊！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "行业观点", "value": "全球模型军备竞赛升级"},
                    {"label": "未来预期", "value": "开源普惠打破闭源垄断"}
                ],
                "highlight_text": "全球AI终局大战",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["DeepSeek", "人工智能", "科技融资", "国产大模型", "算力集群"],
        "target_account_type": "news"
    }
]

async def process_task(task_data):
    task_id = task_data["task_id"]
    print("\n" + "=" * 60)
    print(f"🎬 开始处理热点新闻视频: 【{task_data['title']}】")
    print(f"   Task ID: {task_id} | Theme: {task_data['theme']}")
    print("=" * 60)

    # 按照日期时间动态创建归档目录：output/YYYYMMDD/HHMMSS_<task_id>
    now = datetime.now()
    date_folder = now.strftime("%Y%m%d")
    time_prefix = now.strftime("%H%M%S")
    output_dir = os.path.join(PROJECT_ROOT, "output", date_folder, f"{time_prefix}_{task_id}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "xhs_cards"), exist_ok=True)

    renderer_public_dir = os.path.join(PROJECT_ROOT, "renderer", "public")
    db = PipelineDB(os.path.join(PROJECT_ROOT, "data", "pipeline_state.db"))
    dfa = DFAFilter(os.path.join(PROJECT_ROOT, "config", "sensitive_words.txt"))

    # 安全筛查
    for s in task_data["scenes"]:
        hit, w = dfa.contains_sensitive(s["segment_narration"])
        if hit:
            raise ValueError(f"台词敏感词拦截: {w}")

    draft_payload = DraftScriptPayload(**task_data)
    db.create_task(
        task_id=task_id,
        source_url="https://news.hot-digest.local",
        entity_key=draft_payload.entity_key,
        theme=draft_payload.theme,
        target_account_id="douyin-tech-main",
        draft_script=task_data
    )

    # TTS 对齐
    voice = "zh-CN-YunxiNeural" if task_data["target_account_type"] == "tech" else "zh-CN-XiaoxiaoNeural"
    aligner = TTSAligner(voice=voice, rate="+10%")
    aligned_payload = await aligner.align_draft(draft_payload, task_id, renderer_public_dir)

    task_json_path = os.path.join(renderer_public_dir, "tasks", task_id, "aligned_render.json")
    with open(task_json_path, "w", encoding="utf-8") as f:
        json.dump(aligned_payload.model_dump(), f, ensure_ascii=False, indent=2)

    total_sec = aligned_payload.total_duration_in_frames / aligned_payload.fps
    print(f"✅ 音频合成与时间轴对齐完毕！总帧数: {aligned_payload.total_duration_in_frames} (约 {total_sec:.2f} 秒)")
    db.update_task_status(task_id, "TTS_ALIGNED", aligned_render_json=json.dumps(aligned_payload.model_dump(), ensure_ascii=False))

    # Remotion 渲染
    video_out = os.path.join(output_dir, "video.mp4")
    cover_out = os.path.join(output_dir, "cover.png")
    xhs_card_out = os.path.join(output_dir, "xhs_cards", "card_0.png")

    props_rel_path = f"./public/tasks/{task_id}/aligned_render.json"
    renderer_dir = os.path.join(PROJECT_ROOT, "renderer")

    print(f"  -> 渲染短视频成片: {video_out}")
    cmd_video = [
        "npx", "remotion", "render", "src/index.ts", "GeekShortVideo", video_out,
        f"--props={props_rel_path}",
        "--codec=h264", "--crf=18", "--pixel-format=yuv420p"
    ]
    subprocess.check_call(cmd_video, cwd=renderer_dir)

    print(f"  -> 渲染抖音九宫格封面海报: {cover_out}")
    cmd_cover = [
        "npx", "remotion", "still", "src/index.ts", "CoverStill", cover_out,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_cover, cwd=renderer_dir)

    print(f"  -> 导出小红书 3:4 极客图文卡片: {xhs_card_out}")
    cmd_xhs = [
        "npx", "remotion", "still", "src/index.ts", "XhsCardStill", xhs_card_out,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_xhs, cwd=renderer_dir)

    db.update_task_status(
        task_id,
        "RENDERED",
        video_path=video_out,
        cover_path=cover_out,
        xhs_cards_dir=os.path.join(output_dir, "xhs_cards")
    )

    size_mb = os.path.getsize(video_out) / (1024 * 1024)
    print(f"🎉 视频生成完成！已归档至: {output_dir}")
    return {
        "task_id": task_id,
        "title": task_data["title"],
        "output_dir": output_dir,
        "video_path": video_out,
        "cover_path": cover_out,
        "xhs_card_path": xhs_card_out,
        "size_mb": size_mb,
        "total_sec": total_sec
    }

async def main():
    results = []
    for t in NEWS_TASKS:
        res = await process_task(t)
        results.append(res)
    print("\n" + "=" * 60)
    print("🚀 全部热点新闻视频批量渲染完毕！")
    for r in results:
        print(f"  - 【{r['title']}】\n    归档: {r['output_dir']}\n    规格: {r['size_mb']:.2f} MB, {r['total_sec']:.1f}s")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
