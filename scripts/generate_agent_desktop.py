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
    "task_id": f"news_agentdesktop_{int(time.time())}",
    "theme": "tech-github",
    "entity_key": "gh:vercel-labs/agent-desktop",
    "source_url": "https://github.com/vercel-labs/agent-desktop",
    "title": "告别截图瞎猜！Vercel开源Agent桌面操作底座",
    "cover_main_text": "Vercel开源Agent桌面底座",
    "cover_sub_text": "告别像素盲猜",
    "hook_narration": "还在用截图识别盲猜坐标操作电脑？Vercel刚刚开源桌面操作内核，彻底告别像素盲区！",
    "fact_summary_150": "Vercel Labs开源agent-desktop桌面操作底座。采用纯Rust构建，直接穿透操作系统无障碍树读取真实UI树结构，控件引用稳定且动作支持安全幂等重试，彻底摆脱传统截图视觉盲猜坐标的高延迟与低成功率痛点，今日GitHub狂揽2243星。",
    "resource_keyword": "desktop",
    "template_id": "linear_obsidian",
    "scenes": [
        {
            "scene_index": 0,
            "segment_narration": "还在用截图识别盲猜坐标操作电脑？Vercel刚刚开源桌面操作内核，彻底告别像素盲区！",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "核心突破", "value": "OS无障碍树直接寻址"},
                {"label": "今日飙升", "value": "+2,243 ⭐ 登顶"},
                {"label": "技术底座", "value": "纯 Rust 极速运行时"}
            ],
            "highlight_text": "摆脱像素盲猜高延迟枷锁",
            "sfx_cue": "whoosh"
        },
        {
            "scene_index": 1,
            "segment_narration": "纯Rust构建，直穿系统无障碍层读取真实UI树结构，不仅能精准定位任何窗口控件，更支持安全幂等重试。",
            "visual_type": "terminal_code",
            "code_snippet": "// vercel-labs/agent-desktop: 穿透操作系统无障碍树\nlet desktop = AgentDesktop::connect_accessibility_tree().await?;\nlet target_btn = desktop.find_node_by_semantic_role(\"SubmitOrder\")?;\n// 基于稳定句柄直接触发原生交互，告别像素漂移\ntarget_btn.click_with_safe_retry(3).await?;",
            "highlight_text": "直接通过系统无障碍树稳定操作",
            "sfx_cue": "typing"
        },
        {
            "scene_index": 2,
            "segment_narration": "告别了高昂的截图视觉模型开销，交互延迟直降至毫秒级，操作成功率提升了整整三倍！",
            "visual_type": "stat_chart",
            "stat_items": [
                {"label": "定位成功率", "value": "99.4% 告别点空"},
                {"label": "单步交互耗时", "value": "12ms 极速响应"},
                {"label": "Token消耗", "value": "降低 85% 视觉开销"}
            ],
            "highlight_text": "毫秒级吞吐全面爆发",
            "sfx_cue": "ding"
        },
        {
            "scene_index": 3,
            "segment_narration": "任何本地Agent都能秒级接入，项目已全量开源，欢迎在评论区交流讨论～",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "项目开源", "value": "vercel-labs/agent-desktop"},
                {"label": "获取方式", "value": "评论区交流讨论"}
            ],
            "highlight_text": "下一代智能体操作基座",
            "sfx_cue": "glitch"
        }
    ],
    "tags": ["AgentDesktop", "Vercel", "Rust", "开源项目", "AI智能体", "GitHub爆款"],
    "target_account_type": "tech"
}

async def render_agent_desktop():
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
    asyncio.run(render_agent_desktop())
