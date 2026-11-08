import asyncio
import os
import sys
import json
import time
import subprocess
from pathlib import Path

PROJECT_ROOT = Path('/Users/a1/Downloads/workspace/team/project/autocontent-pipeline')
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / '.env')

from src.tts.aligner import TTSAligner
from src.schemas import DraftScriptPayload
from src.feishu.approval_service import FeishuApprovalService
from src.feishu.card_builder import FeishuCardBuilder
from src.db import PipelineDB

TASK_DATA = {
    "task_id": f"news_catwu_openai_{int(time.time())}",
    "theme": "ai-product",
    "entity_key": "custom:catwu_joins_openai",
    "source_url": "https://news.ycombinator.com",
    "title": "硅谷大地震！Claude操盘手Cat Wu闪电加盟OpenAI",
    "cover_main_text": "硅谷人事大地震",
    "cover_sub_text": "Claude操盘手投奔OpenAI",
    "hook_narration": "硅谷今天突发大地震！刚刚告别Anthropic的顶流操盘手猫姐，正式官宣加盟OpenAI！",
    "fact_summary_150": "AI行业年度最炸裂人才变动：一手操盘Claude Code与Cowork、定义了智能编程范式的核心负责人Cat Wu（猫姐），刚刚正式官宣加盟OpenAI出任核心产品负责人！OpenAI下代编程Agent即将正面迎战Claude！",
    "resource_keyword": "catwu",
    "template_id": "linear_obsidian",
    "scenes": [
        {
            "scene_index": 0,
            "segment_narration": "硅谷今天突发大地震！刚刚告别Anthropic的顶流操盘手猫姐，正式官宣加盟OpenAI！",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "突发震荡", "value": "Cat Wu (猫姐) 官宣跳槽 🚀"},
                {"label": "上一站光环", "value": "一手操刀 Claude Code 🏆"},
                {"label": "最新归宿", "value": "执掌 OpenAI 核心产品线 ⚡"}
            ],
            "highlight_text": "Claude核心产品操盘手闪电加盟OpenAI",
            "sfx_cue": "whoosh"
        },
        {
            "scene_index": 1,
            "segment_narration": "作为上一代AI代码工作流与终端智能体的定义者，猫姐直接带火了Claude Code与协同工作台！",
            "visual_type": "stat_chart",
            "stat_items": [
                {"label": "操盘项目", "value": "Claude Code 终端交互标杆 💻"},
                {"label": "协同范式", "value": "Cowork 工作流开创者 🛠️"},
                {"label": "产品战力", "value": "极客开发者口碑顶流 📈"}
            ],
            "highlight_text": "定义上一代终端AI编程范式",
            "sfx_cue": "typing"
        },
        {
            "scene_index": 2,
            "segment_narration": "这次她将直接出任OpenAI核心产品负责人，意味着奥特曼要在编程Agent与人机交互上全面发力！",
            "visual_type": "terminal_code",
            "code_snippet": "# The Next-Gen Developer Agent War\nAgent_Ecosystem = {\n  \"Anthropic\": \"Claude Code & Sonnet 4.5\",\n  \"OpenAI_New_Move\": \"Cat Wu leading Core Products\",\n  \"Target\": \"Terminal AI & Operator Agent Battle\"\n}\nprint('The Agent interface battle officially begins.')",
            "highlight_text": "OpenAI重注终端Agent与下一代交互",
            "sfx_cue": "ding"
        },
        {
            "scene_index": 3,
            "segment_narration": "但极客客观泼冷水：光换将帅不够，如何把GPT底层模型的逻辑粘性与工具调用做得比Claude更顺手才是硬骨头！",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "独家洞察", "value": "交互体验将成Agent终局胜负手 💡"},
                {"label": "客观泼冷水", "value": "底层模型工具链粘性仍需死磕 ⚠️"},
                {"label": "行业看点", "value": "两大巨头开发者生态全面火拼 ⚔️"}
            ],
            "highlight_text": "极客洞察：换将不够，模型与交互融合是硬骨头",
            "sfx_cue": "glitch"
        },
        {
            "scene_index": 4,
            "segment_narration": "你看好猫姐在OpenAI打造出超越Claude的神级产品吗？欢迎在评论区交流讨论～",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "今日议题", "value": "OpenAI 能在编程Agent反超Claude吗？🤔"},
                {"label": "履历背景", "value": "详见视频简介与首评置顶 📄"},
                {"label": "极客交流", "value": "欢迎在评论区交流讨论～"}
            ],
            "highlight_text": "欢迎在评论区交流讨论～",
            "sfx_cue": "whoosh"
        }
    ],
    "tags": ["CatWu", "OpenAI", "Anthropic", "ClaudeCode", "人工智能"],
    "target_account_type": "tech"
}

async def render_catwu_video():
    payload = DraftScriptPayload(**TASK_DATA)
    task_id = TASK_DATA['task_id']
    date_str = 'catwu_spotlight'
    
    db = PipelineDB(str(PROJECT_ROOT / 'data' / 'pipeline_state.db'))
    db.create_task(
        task_id=task_id,
        source_url=TASK_DATA['source_url'],
        entity_key=TASK_DATA['entity_key'],
        theme=TASK_DATA['theme'],
        target_account_id='bili-tech-main',
        draft_script=TASK_DATA
    )
    
    renderer_dir = PROJECT_ROOT / 'renderer'
    renderer_public_dir = str(renderer_dir / 'public')
    
    print(f"[1/4] 合成语音并对齐时间轴: {task_id}...")
    aligner = TTSAligner(voice='zh-CN-YunxiNeural', rate='+10%')
    aligned_payload = await aligner.align_draft(payload, task_id, renderer_public_dir)
    
    task_json_path = renderer_dir / 'public' / 'tasks' / task_id / 'aligned_render.json'
    task_json_path.parent.mkdir(parents=True, exist_ok=True)
    task_json_path.write_text(json.dumps(aligned_payload.model_dump(), ensure_ascii=False, indent=2))
    
    output_dir = PROJECT_ROOT / 'output' / date_str / f'task_{task_id}'
    output_dir.mkdir(parents=True, exist_ok=True)
    video_portrait_out = str(output_dir / f'{task_id}_video_portrait_9_16.mp4')
    cover_portrait_out = str(output_dir / f'{task_id}_cover_portrait_9_16.png')
    video_horiz_out = str(output_dir / f'{task_id}_video_horizontal_16_9.mp4')
    cover_horiz_out = str(output_dir / f'{task_id}_cover_horizontal_16_9.png')
    props_rel_path = f'./public/tasks/{task_id}/aligned_render.json'
    
    # 清空 remotion 临时缓存，杜绝旧资产污染
    subprocess.run(['rm', '-rf', str(renderer_dir / '.cache')], check=False)
    
    print(f"[2/4] Remotion 渲染 9:16 竖版视频成片...")
    cmd_v_portrait = [
        'npx', 'remotion', 'render', 'src/index.ts', 'GeekShortVideo', video_portrait_out,
        f'--props={props_rel_path}',
        '--codec=h264', '--crf=18', '--pixel-format=yuv420p'
    ]
    subprocess.check_call(cmd_v_portrait, cwd=renderer_dir)
    
    cmd_c_portrait = [
        'npx', 'remotion', 'still', 'src/index.ts', 'CoverStill', cover_portrait_out,
        f'--props={props_rel_path}', '--image-format=png'
    ]
    subprocess.check_call(cmd_c_portrait, cwd=renderer_dir)
    
    # 横屏统一走权威入口 render_landscape_bilibili（BilibiliLandscapeVideo + BilibiliCoverStill + 三项出厂断言）
    print(f"[3/4] 通过统一入口渲染 16:9 横屏成片与封面...")
    from scripts.render_landscape_bilibili import render_landscape
    landscape_res = render_landscape(
        task_id=task_id,
        props_rel=props_rel_path,
        output_dir=str(output_dir),
        update_db=False,
        verbose=True,
    )
    video_horiz_out = landscape_res["video"]
    cover_horiz_out = landscape_res["cover"]

    db.update_task_status(task_id, 'WAITING_APPROVAL', video_path=video_portrait_out, cover_path=cover_portrait_out)
    video_size_mb = round(os.path.getsize(video_portrait_out) / (1024 * 1024), 2)
    horiz_size_mb = round(os.path.getsize(video_horiz_out) / (1024 * 1024), 2)
    total_sec = round(aligned_payload.total_duration_in_frames / aligned_payload.fps, 1)
    print(f"成片渲染全部成功! 竖屏: {video_size_mb}MB, 横屏: {horiz_size_mb}MB, 时长: {total_sec}s")
    
    print(f"[4/4] 推送飞书双画幅原片与终审卡片...")
    feishu = FeishuApprovalService()
    chat_id = os.getenv('FEISHU_DEFAULT_CHAT_ID', 'oc_c3e01371d3bc916ef3f2af8b904eb785')
    rec_type = 'chat_id'
    
    v1_msg = feishu.upload_and_send_video(receive_id=chat_id, receive_id_type=rec_type, video_path=video_portrait_out)
    v2_msg = feishu.upload_and_send_video(receive_id=chat_id, receive_id_type=rec_type, video_path=video_horiz_out)
    print(f"飞书双画幅原片推送成功: 竖屏={v1_msg}, 横屏={v2_msg}")
    
    card = FeishuCardBuilder.build_approval_card(
        task_id=task_id,
        title=f"【硅谷人事巨震】{TASK_DATA['title']}",
        topics=TASK_DATA['tags'],
        content_text=f"💥 Claude Code操盘手Cat Wu闪电加盟OpenAI出任核心产品负责人！包含9:16竖屏({video_size_mb}MB)与16:9横屏({horiz_size_mb}MB)双模态原片",
        resource_keyword=TASK_DATA['resource_keyword'],
        fact_summary=TASK_DATA['fact_summary_150'],
        duration_sec=total_sec,
        video_size_mb=video_size_mb,
        template_id=TASK_DATA['template_id'],
        status='PENDING_APPROVAL'
    )
    c_msg = feishu.send_approval_card(receive_id=chat_id, receive_id_type=rec_type, card_content=card)
    print(f"飞书终审审批卡片推送成功: {c_msg}")
    print("ALL_CATWU_PIPELINE_SUCCESS")

if __name__ == '__main__':
    asyncio.run(render_catwu_video())
