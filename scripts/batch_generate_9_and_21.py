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

TASKS_DATA = [
    {
        "task_id": f"news_deepseek_creator_{int(time.time())}",
        "theme": "ai-product",
        "entity_key": "tm:deepseek_creator_mode",
        "source_url": "https://www.techmeme.com/261102/p32#a261102p32",
        "title": "DeepSeek创作者模式上线！一键打造3D与视频插件",
        "cover_main_text": "DeepSeek创作者模式",
        "cover_sub_text": "一键打造3D与视频插件",
        "hook_narration": "生态大爆发！DeepSeek上线创作者模式，做插件只要动动嘴！",
        "fact_summary_150": "彭博社报道DeepSeek正式上线创作者模式。用户可通过DeepSeek Harness快速构建自定义插件，如3D模型渲染器和视频剪辑工作台，零代码拖拽生成复杂工具链，生态开发门槛彻底打破。",
        "resource_keyword": "creator",
        "template_id": "cupertino_glass",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "生态大爆发！DeepSeek上线创作者模式，做插件只要动动嘴！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "官方重磅", "value": "创作者模式全量上线"},
                    {"label": "核心平台", "value": "DeepSeek Harness"},
                    {"label": "生态突破", "value": "零代码生成自定义插件"}
                ],
                "highlight_text": "大模型生态开发者红利爆发",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "Harness平台内置低代码画布，一句话就能自适应生成3D渲染器或视频剪辑工作流，开发门槛彻底归零。",
                "visual_type": "terminal_code",
                "code_snippet": "// DeepSeek Harness 创作者模式：插件一键声明\nexport default definePlugin({\n  name: 'instant-3d-renderer',\n  runtime: 'deepseek-v4-vision',\n  actions: ['renderMesh', 'exportVideoTrack'],\n  onPrompt: async (ctx) => ctx.buildPipeline()\n});\n>> [Success] 3D 工作台插件已就绪并在端侧挂载！",
                "highlight_text": "自然语言实时构建工业级插件",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 2,
                "segment_narration": "插件生成耗时从数天缩短至两分钟，社区首日涌现上百款极客工具，生态护城河全面成型！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "开发周期", "value": "数天 -> 2分钟极速出炉"},
                    {"label": "首日插件数", "value": "100+ 极客工具井喷"},
                    {"label": "多模态覆盖", "value": "3D渲染 / 视频剪辑全通"}
                ],
                "highlight_text": "开发效率实现百倍跃升",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 3,
                "segment_narration": "开发者的超级杠杆已经来了，赶紧去探索属于你的专属插件吧，欢迎在评论区交流讨论～",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "支持场景", "value": "3D工作台 / 视频流转"},
                    {"label": "极客互动", "value": "欢迎在评论区交流讨论～"}
                ],
                "highlight_text": "欢迎在评论区交流讨论～",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["DeepSeek", "大模型插件", "3D渲染", "视频工作流", "AI生态", "科技突发"],
        "target_account_type": "tech"
    },
    {
        "task_id": f"news_deepseek_coding_agent_{int(time.time()) + 1}",
        "theme": "breaking-news",
        "entity_key": "tw:deepseek_autonomous_coding_agent",
        "source_url": "https://x.com/i/status/2117023594513338615",
        "title": "正面硬刚Claude！DeepSeek自主编程智能体震撼发布",
        "cover_main_text": "正面硬刚Claude Code",
        "cover_sub_text": "DeepSeek编程Agent发布",
        "hook_narration": "编程智能体决战打响！DeepSeek刚刚推出自主编码Agent，正面硬刚Claude！",
        "fact_summary_150": "Investing.com报道DeepSeek正式推出端到端自主编程智能体，直接对标Anthropic Claude Code。具备完全自主的终端指令执行、自愈排错与项目级架构重构能力，推理成本仅海外方案数分之一，重塑AI协同编码新格局。",
        "resource_keyword": "codingagent",
        "template_id": "linear_obsidian",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "编程智能体决战打响！DeepSeek刚刚推出自主编码Agent，正面硬刚Claude！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "重磅发布", "value": "DeepSeek 自主编程 Agent"},
                    {"label": "对标目标", "value": "Anthropic Claude Code"},
                    {"label": "核心优势", "value": "端到端终端自愈排错"}
                ],
                "highlight_text": "AI协同编码新时代到来",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "具备端到端终端控制与自愈式排错能力，从需求拆解到写代码、跑测试再到修Bug，全链路自主闭环。",
                "visual_type": "terminal_code",
                "code_snippet": "# DeepSeek Autonomous Coding Agent 运行日志\n$ deepseek-code run --task \"fix memory leak in Rust service\"\n>> Reading ast & profiling heap alloc...\n>> Patching src/worker.rs (replacing Arc with Arena)...\n>> Running cargo test --workspace [PASS 42/42]\n>> PR created: feat/opt-mem-leak #104 [Ready]",
                "highlight_text": "需求分析到测试自愈全自主闭环",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 2,
                "segment_narration": "真实软件工程评测拿下顶级高分，推理成本仅为海外闭源方案的几十分之一！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "SWE-bench", "value": "顶级基准高分通过"},
                    {"label": "推理成本", "value": "仅海外竞品 1/30"},
                    {"label": "运行模式", "value": "完全离线 / 自主沙箱"}
                ],
                "highlight_text": "极致性能打破垄断壁垒",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 3,
                "segment_narration": "开源与低成本正在重塑AI软件工程新规则，你觉得谁更能打？欢迎在评论区交流讨论～",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "未来展望", "value": "全自主研发体系"},
                    {"label": "极客互动", "value": "欢迎在评论区交流讨论～"}
                ],
                "highlight_text": "欢迎在评论区交流讨论～",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["DeepSeek", "ClaudeCode", "编程智能体", "AI写代码", "开源AI", "科技先锋"],
        "target_account_type": "tech"
    }
]

async def process_batch():
    db = PipelineDB(str(PROJECT_ROOT / 'data' / 'pipeline_state.db'))
    feishu = FeishuApprovalService()
    chat_id = os.getenv('FEISHU_DEFAULT_CHAT_ID', 'oc_c3e01371d3bc916ef3f2af8b904eb785')
    rec_type = 'chat_id'
    
    renderer_dir = PROJECT_ROOT / 'renderer'
    renderer_public_dir = str(renderer_dir / 'public')
    date_str = '20261102'

    for idx, task_data in enumerate(TASKS_DATA, 1):
        task_id = task_data['task_id']
        payload = DraftScriptPayload(**task_data)
        print(f"\n==========================================")
        print(f"🎬 开始处理任务 [{idx}/2]: {task_data['title']} ({task_id})")
        print(f"==========================================")

        # 1. 写入 SQLite
        db.create_task(
            task_id=task_id,
            source_url=task_data['source_url'],
            entity_key=task_data['entity_key'],
            theme=task_data['theme'],
            target_account_id='tech-main',
            draft_script=task_data
        )

        # 2. TTS 对齐
        print(f"[1/3] 合成语音并对齐字级时间戳...")
        aligner = TTSAligner.from_slot("tts.tech")
        aligned_payload = await aligner.align_draft(payload, task_id, renderer_public_dir)

        task_json_path = renderer_dir / 'public' / 'tasks' / task_id / 'aligned_render.json'
        task_json_path.parent.mkdir(parents=True, exist_ok=True)
        task_json_path.write_text(json.dumps(aligned_payload.model_dump(), ensure_ascii=False, indent=2))

        # 3. Remotion 渲染
        output_dir = PROJECT_ROOT / 'output' / date_str / f'task_{task_id}'
        output_dir.mkdir(parents=True, exist_ok=True)
        video_out = str(output_dir / 'video.mp4')
        cover_out = str(output_dir / 'cover.png')
        props_rel_path = f'./public/tasks/{task_id}/aligned_render.json'

        print(f"[2/3] Remotion 渲染视频成片...")
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

        # 4. 推送飞书
        print(f"[3/3] 推送飞书原片与审批卡片...")
        v_msg_id = feishu.upload_and_send_video(receive_id=chat_id, receive_id_type=rec_type, video_path=video_out)
        print(f"飞书原片推送成功: {v_msg_id}")

        card = FeishuCardBuilder.build_approval_card(
            task_id=task_id,
            title=task_data['title'],
            topics=task_data['tags'],
            content_text=task_data['hook_narration'],
            resource_keyword=task_data['resource_keyword'],
            fact_summary=task_data['fact_summary_150'],
            duration_sec=total_sec,
            video_size_mb=video_size_mb,
            template_id=task_data['template_id'],
            status='PENDING_APPROVAL'
        )
        c_msg_id = feishu.send_approval_card(receive_id=chat_id, receive_id_type=rec_type, card_content=card)
        print(f"飞书审批卡片推送成功: {c_msg_id}")
        time.sleep(1.5)

    print("\n🎉 全部批量制作任务顺利完成！")

if __name__ == '__main__':
    asyncio.run(process_batch())
