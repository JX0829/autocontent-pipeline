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
    "task_id": f"news_cursor_deepseek_{int(time.time())}",
    "theme": "ai-product",
    "entity_key": "tw:cursor_ai:deepseek_v4_1_flash",
    "source_url": "https://x.com/i/status/2114839997568405608",
    "title": "Cursor官方接入DeepSeek！编码成本直降十倍",
    "cover_main_text": "Cursor接入DeepSeek",
    "cover_sub_text": "编码成本直降十倍",
    "hook_narration": "性价比神器来了！Cursor官方接入DeepSeek，编码成本直接打骨折！",
    "fact_summary_150": "Cursor官方正式全量上线DeepSeek V4.1 Flash。在权威CursorBench评测中，单任务成本仅需0.60至0.98美元，高分性能超越同级模型，直接在Cursor设置Models面板一键开启，推文引发海外超36万极客热议。",
    "resource_keyword": "cursor",
    "template_id": "linear_obsidian",
    "scenes": [
        {
            "scene_index": 0,
            "segment_narration": "性价比神器来了！Cursor官方正式接入DeepSeek，编码成本直接打骨折！",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "官方重磅", "value": "Cursor 全量上线"},
                {"label": "核心模型", "value": "DeepSeek V4.1 Flash"},
                {"label": "实测表现", "value": "CursorBench 性价比登顶"}
            ],
            "highlight_text": "AI协同编码成本革命爆发",
            "sfx_cue": "whoosh"
        },
        {
            "scene_index": 1,
            "segment_narration": "无需第三方代理转发，直接在Cursor设置里的Models面板一键勾选，原生智能体上下文秒级响应。",
            "visual_type": "terminal_code",
            "code_snippet": "// Cursor 官方原生模型接入配置\n{\n  \"models\": {\n    \"deepseek-v4.1-flash\": {\n      \"enabled\": true,\n      \"context_window\": \"128k\",\n      \"agent_mode\": \"full_autonomous\",\n      \"benchmark_cost\": \"$0.60/task\"\n    }\n  }\n}",
            "highlight_text": "设置面板一键开启原生支持",
            "sfx_cue": "typing"
        },
        {
            "scene_index": 2,
            "segment_narration": "官方基准实测，单任务成本低至六毛钱，同等高分表现下，没有大模型比它更能打！",
            "visual_type": "stat_chart",
            "stat_items": [
                {"label": "单任务成本", "value": "$0.60 ~ $0.98 极致降本"},
                {"label": "CursorBench", "value": "高难度任务得分领跑"},
                {"label": "海外热度", "value": "36万浏览 / 4000+点赞"}
            ],
            "highlight_text": "极致成本重塑编程生产力",
            "sfx_cue": "ding"
        },
        {
            "scene_index": 3,
            "segment_narration": "动动手指就能把开发效率拉满，赶紧去设置里打开体验吧，欢迎在评论区交流讨论～",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "适用场景", "value": "代码补全 / 重构 / 排错"},
                {"label": "推荐操作", "value": "设置面板立即开启"}
            ],
            "highlight_text": "欢迎在评论区交流讨论～",
            "sfx_cue": "glitch"
        }
    ],
    "tags": ["Cursor", "DeepSeek", "AI编程", "开源大模型", "极客生产力", "科技新闻"],
    "target_account_type": "tech"
}

async def render_cursor_video():
    payload = DraftScriptPayload(**TASK_DATA)
    task_id = TASK_DATA['task_id']
    date_str = '20261102'
    
    db = PipelineDB(str(PROJECT_ROOT / 'data' / 'pipeline_state.db'))
    db.create_task(
        task_id=task_id,
        source_url=TASK_DATA['source_url'],
        entity_key=TASK_DATA['entity_key'],
        theme=TASK_DATA['theme'],
        target_account_id='tech-main',
        draft_script=TASK_DATA
    )
    
    renderer_dir = PROJECT_ROOT / 'renderer'
    renderer_public_dir = str(renderer_dir / 'public')
    
    print(f"[1/3] 合成语音并对齐字级时间戳: {task_id}...")
    aligner = TTSAligner(voice='zh-CN-YunxiNeural', rate='+10%')
    aligned_payload = await aligner.align_draft(payload, task_id, renderer_public_dir)
    
    task_json_path = renderer_dir / 'public' / 'tasks' / task_id / 'aligned_render.json'
    task_json_path.parent.mkdir(parents=True, exist_ok=True)
    task_json_path.write_text(json.dumps(aligned_payload.model_dump(), ensure_ascii=False, indent=2))
    
    output_dir = PROJECT_ROOT / 'output' / date_str / f'task_{task_id}'
    output_dir.mkdir(parents=True, exist_ok=True)
    video_out = str(output_dir / 'video.mp4')
    cover_out = str(output_dir / 'cover.png')
    props_rel_path = f'./public/tasks/{task_id}/aligned_render.json'
    
    print(f"[2/3] Remotion 渲染短视频成片...")
    cmd_video = [
        'npx', 'remotion', 'render', 'src/index.ts', 'GeekShortVideo', video_out,
        f'--props={props_rel_path}',
        '--codec=h264', '--crf=18', '--pixel-format=yuv420p'
    ]
    subprocess.check_call(cmd_video, cwd=renderer_dir)
    
    cmd_cover = [
        'npx', 'remotion', 'still', 'src/index.ts', 'CoverStill', cover_out,
        f'--props={props_rel_path}', '--image-format=png'
    ]
    subprocess.check_call(cmd_cover, cwd=renderer_dir)
    
    db.update_task_status(task_id, 'WAITING_APPROVAL', video_path=video_out, cover_path=cover_out)
    video_size_mb = round(os.path.getsize(video_out) / (1024 * 1024), 2)
    total_sec = round(aligned_payload.total_duration_in_frames / aligned_payload.fps, 1)
    print(f"成片成功: {video_out} ({video_size_mb} MB, {total_sec}s)")
    
    print(f"[3/3] 推送飞书原片与审批卡片...")
    feishu = FeishuApprovalService()
    chat_id = os.getenv('FEISHU_DEFAULT_CHAT_ID', 'oc_c3e01371d3bc916ef3f2af8b904eb785')
    rec_type = 'chat_id'
    
    v_msg_id = feishu.upload_and_send_video(receive_id=chat_id, receive_id_type=rec_type, video_path=video_out)
    print(f"飞书原片推送成功: {v_msg_id}")
    
    card = FeishuCardBuilder.build_approval_card(
        task_id=task_id,
        title=TASK_DATA['title'],
        topics=TASK_DATA['tags'],
        content_text=TASK_DATA['hook_narration'],
        resource_keyword=TASK_DATA['resource_keyword'],
        fact_summary=TASK_DATA['fact_summary_150'],
        duration_sec=total_sec,
        video_size_mb=video_size_mb,
        template_id=TASK_DATA['template_id'],
        status='PENDING_APPROVAL'
    )
    c_msg_id = feishu.send_approval_card(receive_id=chat_id, receive_id_type=rec_type, card_content=card)
    print(f"飞书审批卡片推送成功: {c_msg_id}")
    print("ALL_TASK_FINISHED_SUCCESS")

if __name__ == '__main__':
    asyncio.run(render_cursor_video())
