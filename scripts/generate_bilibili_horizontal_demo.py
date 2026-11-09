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

DEMO_HORIZONTAL_TASK = {
    "task_id": f"bili_horizontal_dsh_{int(time.time())}",
    "theme": "ai-product",
    "entity_key": "bili:DeepSeekHarness:horizontal",
    "source_url": "https://www.bilibili.com/video/BV1i24P6rEmt",
    "title": "梁神又掀桌了！一句话用DeepSeek造原生插件",
    "cover_main_text": "DeepSeek造插件",
    "cover_sub_text": "16:9横屏旗舰重构",
    "hook_narration": "还在到处找插件？DeepSeek上线创作者模式，说句人话就能自动手搓工具！",
    "fact_summary_150": "DeepSeek官方正式上线创作者模式。用户可通过自然语言快速构建专属插件，包括3D模型渲染器和视频工作台，B站相关视频引发现象级讨论，单日狂揽超83万播放，彻底颠覆传统软件开发门槛。",
    "resource_keyword": "harness",
    "template_id": "linear_obsidian",
    "scenes": [
        {
            "scene_index": 0,
            "segment_narration": "还在到处找插件？DeepSeek上线创作者模式，说句人话就能自动手搓工具！",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "生态突破", "value": "自然语言生成插件"},
                {"label": "开发门槛", "value": "零代码直接上手"},
                {"label": "B站热度", "value": "83万播放全站登顶"}
            ],
            "highlight_text": "人话手搓工具链时代到来",
            "sfx_cue": "whoosh"
        },
        {
            "scene_index": 1,
            "segment_narration": "无论是3D模型交互渲染，还是专属视频剪辑面板，只要描述工作流，它就能自动生成可用插件。",
            "visual_type": "terminal_code",
            "code_snippet": "// DeepSeek Harness: Creator Mode\nlet plugin = harness.create_plugin({\n  target: \"3D-Renderer\",\n  prompt: \"渲染带动态阴影的GLTF模型并支持鼠标旋转\"\n});\nplugin.mount_and_run();",
            "highlight_text": "对话直出生产力插件",
            "sfx_cue": "typing"
        },
        {
            "scene_index": 2,
            "segment_narration": "不仅完全开源免配置，更让普通开发者拥有了无限扩展超级智能体的可能！",
            "visual_type": "stat_chart",
            "stat_items": [
                {"label": "开发耗时", "value": "从3天缩至30秒"},
                {"label": "扩展能力", "value": "100% 任意定制"},
                {"label": "源码开放", "value": "免费开源可自建"}
            ],
            "highlight_text": "超级智能体终极进化",
            "sfx_cue": "ding"
        },
        {
            "scene_index": 3,
            "segment_narration": "官方提示词库与项目直链已整理在视频简介与置顶，欢迎在评论区交流讨论～",
            "visual_type": "ui_card",
            "stat_items": [
                {"label": "源码与提示词", "value": "见视频简介与置顶"},
                {"label": "交流互动", "value": "欢迎在评论区交流讨论～"}
            ],
            "highlight_text": "完整项目见简介与置顶",
            "sfx_cue": "glitch"
        }
    ],
    "tags": ["DeepSeek", "B站横屏", "AI工具", "编程", "开源项目", "科技旗舰"],
    "target_account_type": "tech"
}

async def render_horizontal_demo():
    payload = DraftScriptPayload(**DEMO_HORIZONTAL_TASK)
    task_id = DEMO_HORIZONTAL_TASK['task_id']
    date_str = 'demo_bilibili_horizontal'
    
    # 1. 记录到数据库
    db = PipelineDB(str(PROJECT_ROOT / 'data' / 'pipeline_state.db'))
    db.create_task(
        task_id=task_id,
        source_url=DEMO_HORIZONTAL_TASK['source_url'],
        entity_key=DEMO_HORIZONTAL_TASK['entity_key'],
        theme=DEMO_HORIZONTAL_TASK['theme'],
        target_account_id='bili-tech-main',
        draft_script=DEMO_HORIZONTAL_TASK
    )
    
    renderer_dir = PROJECT_ROOT / 'renderer'
    renderer_public_dir = str(renderer_dir / 'public')
    
    print(f"[1/3] 合成语音并对齐字级时间戳: {task_id}...")
    aligner = TTSAligner.from_slot("tts.tech")
    aligned_payload = await aligner.align_draft(payload, task_id, renderer_public_dir)
    
    # 覆写为 16:9 横屏规格 (1920x1080)
    aligned_dict = aligned_payload.model_dump()
    aligned_dict['width'] = 1920
    aligned_dict['height'] = 1080
    
    task_json_path = renderer_dir / 'public' / 'tasks' / task_id / 'aligned_render.json'
    task_json_path.parent.mkdir(parents=True, exist_ok=True)
    task_json_path.write_text(json.dumps(aligned_dict, ensure_ascii=False, indent=2))
    
    output_dir = PROJECT_ROOT / 'output' / date_str / f'task_{task_id}'
    output_dir.mkdir(parents=True, exist_ok=True)
    video_out = str(output_dir / 'video_horizontal.mp4')
    cover_out = str(output_dir / 'cover_horizontal.png')
    props_rel_path = f'./public/tasks/{task_id}/aligned_render.json'
    
    # 横屏统一走权威入口 render_landscape（landscape_guard：像素覆盖 + 边缘留白 + 首帧=封面）
    # 旧入口 render_landscape_bilibili 的 A1–A3 断言抓不到「竖版封面塞横屏」，已废弃
    from scripts.render_landscape import render_landscape
    _lr = render_landscape(
        task_id=task_id,
        props_rel=props_rel_path,
        output_dir=str(Path(video_out).parent),
        update_db=False,
        verbose=True,
    )
    video_out = _lr["video"]
    cover_out = _lr["cover"]

    db.update_task_status(task_id, 'WAITING_APPROVAL', video_path=video_out, cover_path=cover_out)
    video_size_mb = round(os.path.getsize(video_out) / (1024 * 1024), 2)
    total_sec = round(aligned_payload.total_duration_in_frames / aligned_payload.fps, 1)
    print(f"成片成功: {video_out} ({video_size_mb} MB, {total_sec}s)")
    
    print(f"[3/3] 推送飞书原片与审批卡片...")
    feishu = FeishuApprovalService()
    chat_id = os.getenv('FEISHU_DEFAULT_CHAT_ID', 'oc_c3e01371d3bc916ef3f2af8b904eb785')
    rec_type = 'chat_id'
    
    v_msg_id = feishu.upload_and_send_video(receive_id=chat_id, receive_id_type=rec_type, video_path=video_out)
    print(f"飞书横屏原片推送成功: {v_msg_id}")
    
    card = FeishuCardBuilder.build_approval_card(
        task_id=task_id,
        title=f"【16:9横屏Demo】{DEMO_HORIZONTAL_TASK['title']}",
        topics=DEMO_HORIZONTAL_TASK['tags'],
        content_text=f"📐 16:9 (1920x1080) B站旗舰横屏画幅：{DEMO_HORIZONTAL_TASK['hook_narration']}",
        resource_keyword=DEMO_HORIZONTAL_TASK['resource_keyword'],
        fact_summary=DEMO_HORIZONTAL_TASK['fact_summary_150'],
        duration_sec=total_sec,
        video_size_mb=video_size_mb,
        template_id=DEMO_HORIZONTAL_TASK['template_id'],
        status='PENDING_APPROVAL'
    )
    c_msg_id = feishu.send_approval_card(receive_id=chat_id, receive_id_type=rec_type, card_content=card)
    print(f"飞书审批卡片推送成功: {c_msg_id}")
    print("ALL_HORIZONTAL_DEMO_FINISHED_SUCCESS")

if __name__ == '__main__':
    asyncio.run(render_horizontal_demo())
