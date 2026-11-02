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
        "task_id": f"news_decisionrank_{int(time.time())}",
        "theme": "ai-product",
        "entity_key": "arxiv:2610.42128v1",
        "source_url": "http://arxiv.org/abs/2610.42128v1",
        "title": "告别逐字解码！DecisionRank让决策重排提速十倍",
        "cover_main_text": "决策模型重排十倍加速",
        "cover_sub_text": "DecisionRank开源",
        "hook_narration": "用自回归模型做重排太慢？DecisionRank砍掉逐字解码！",
        "fact_summary_150": "arXiv前沿团队发布DecisionRank：基于On-Policy策略蒸馏的决策重排架构。彻底规避逐Token自回归解码延迟，通过策略空间直接输出候选排序与结构化得分，重排推理吞吐提升超10倍，全面赋能搜索推荐与Agent规划决策。",
        "resource_keyword": "decision",
        "template_id": "cupertino_glass",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "用自回归模型做搜索重排太慢？DecisionRank直接砍掉漫长解码！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "核心痛点", "value": "自回归解码高延迟"},
                    {"label": "技术路线", "value": "On-Policy 策略蒸馏"},
                    {"label": "性能突破", "value": "决策排序提速 10 倍"}
                ],
                "highlight_text": "摆脱自回归高延迟枷锁",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "它通过将生成式模型的复杂偏好空间，直接蒸馏为非自回归决策打分头，一次前向传播即可锁定全局最优排序。",
                "visual_type": "terminal_code",
                "code_snippet": "# DecisionRank 非自回归极速重排流水线\nfrom decision_rank import DecisionReranker\n\nmodel = DecisionReranker.from_pretrained('decision-rank-v1')\n# 单次前向推理并行打分，彻底杜绝 token-by-token loop\nranked_scores = model.score_candidates(\n    query=agent_state,\n    candidates=action_space,\n    strategy='on_policy_distill'\n)\n>> Top-1 Action: execute_tool_call() [Latency: 8.2ms]",
                "highlight_text": "单次前向直接输出最优决策",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 2,
                "segment_narration": "在大规模搜索与Agent路径评测中，精度超越主流Cross-Encoder，端到端延迟降低至惊人的八毫秒！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "端到端延迟", "value": "8.2ms 极致响应"},
                    {"label": "吞吐量提升", "value": "10.4x 线性爆发"},
                    {"label": "NDCG@10", "value": "89.4 逆势领先"}
                ],
                "highlight_text": "毫秒级吞吐全面爆发",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 3,
                "segment_narration": "论文与代码全量公开，欢迎在评论区交流讨论～",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "论文获取", "value": "arXiv 官方开源"},
                    {"label": "适用场景", "value": "RAG检索 / Agent规划"}
                ],
                "highlight_text": "下一代决策大模型基准",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["DecisionRank", "大模型优化", "Agent决策", "深度学习", "人工智能", "算法论文"],
        "target_account_type": "tech"
    },
    {
        "task_id": f"news_yc_agents_{int(time.time()) + 1}",
        "theme": "breaking-news",
        "entity_key": "techmeme:yc-minecraft-agents",
        "source_url": "https://www.techmeme.com/261102/p4#a261102p4",
        "title": "YC实验室震撼实验：我的世界中AI演化出自主阶级与社会分工",
        "cover_main_text": "AI自主演化出人类社会",
        "cover_sub_text": "YC实验室震撼实测",
        "hook_narration": "AI在自主创造社会！YC公布颠覆实验，AI自发演化出阶级与货币！",
        "fact_summary_150": "YC Labs公布Minecraft多智能体自主演化实验结果：数十个具备长期记忆的智能体在开放世界中展现惊人涌现性。在没有任何预设规则下，AI自发建立起村落阶级、职业分工、契约贸易与协作防卫体系，智能体社会学迎来里程碑突破。",
        "resource_keyword": "ycagents",
        "template_id": "neo_brutalism",
        "scenes": [
            {
                "scene_index": 0,
                "segment_narration": "AI正在自主创造人类社会！YC实验室刚刚公布一项颠覆性实验，多智能体在游戏中演化出了阶级与货币！",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "实验平台", "value": "Minecraft 开放沙盒"},
                    {"label": "核心机制", "value": "无预设自主涌现"},
                    {"label": "震撼发现", "value": "自发形成社会分工"}
                ],
                "highlight_text": "智能体社会的惊人涌现",
                "sfx_cue": "whoosh"
            },
            {
                "scene_index": 1,
                "segment_narration": "研究人员没有写入任何社会规则，仅赋予智能体感知与记忆，AI竟自发分化出农夫、工匠与领袖，甚至发明了以物易物的信用凭证。",
                "visual_type": "terminal_code",
                "code_snippet": "# YC Labs 多智能体自主社会交互日志\n[Tick 1420] Agent_04 (Role: Farmer) -> harvests wheat\n[Tick 1425] Agent_04 -> trades 10 wheat with Agent_12 (Builder) for wood\n[Tick 1500] Agent_01 (Elected Leader) -> organizes village wall defense\n>> Emergent Behavior: Social Contract & Currency Established!",
                "highlight_text": "自发建立契约与经济系统",
                "sfx_cue": "typing"
            },
            {
                "scene_index": 2,
                "segment_narration": "多智能体协作完成复杂任务的成功率提升了四倍，群体智能的演化速度彻底颠覆了学术界预期！",
                "visual_type": "stat_chart",
                "stat_items": [
                    {"label": "任务协作效率", "value": "+410% 飞跃式提升"},
                    {"label": "组织层级涌现", "value": "3级 自发管理结构"},
                    {"label": "生存周期突破", "value": "超 50000 Tick 稳定运行"}
                ],
                "highlight_text": "群体智能颠覆认知",
                "sfx_cue": "ding"
            },
            {
                "scene_index": 3,
                "segment_narration": "这是具身智能体迈向现实社会的必经之路，欢迎在评论区交流讨论～",
                "visual_type": "ui_card",
                "stat_items": [
                    {"label": "报告来源", "value": "YC Labs 官方技术内幕"},
                    {"label": "未来启示", "value": "具身智能多体协同演化"}
                ],
                "highlight_text": "人类文明的硅基镜像",
                "sfx_cue": "glitch"
            }
        ],
        "tags": ["YCLabs", "Minecraft", "多智能体", "具身智能", "群体智能", "科技前沿"],
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
        print(f"[1/3] 合成语音并对齐时间戳...")
        aligner = TTSAligner(voice='zh-CN-YunxiNeural', rate='+10%')
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
