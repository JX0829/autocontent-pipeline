import argparse
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
from src.templates_manager import TemplateManager

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

async def run_phase1_demo(template: str = "auto"):
    print("=" * 60)
    print("🚀 启动 Phase 1 端到端合成演示: 音画两阶段同步与极客短视频渲染")
    print("=" * 60)

    task_id = "demo_task_001"
    mock_file = os.path.join(PROJECT_ROOT, "data", "mock_draft_script.json")
    renderer_public_dir = os.path.join(PROJECT_ROOT, "renderer", "public")
    
    # 按照日期和时间归档目录：output/YYYYMMDD/HHMMSS_<task_id>
    now = datetime.now()
    date_folder = now.strftime("%Y%m%d")
    time_prefix = now.strftime("%H%M%S")
    output_dir = os.path.join(PROJECT_ROOT, "output", date_folder, f"{time_prefix}_{task_id}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "xhs_cards"), exist_ok=True)

    # 1. 初始化 SQLite 状态机
    print("\n[Step 1/5] 初始化 SQLite 状态机...")
    db = PipelineDB(os.path.join(PROJECT_ROOT, "data", "pipeline_state.db"))
    with open(mock_file, "r", encoding="utf-8") as f:
        mock_data = json.load(f)

    # 2. 上游与出厂安全合规审查 (DFA Filter)
    print("\n[Step 2/5] 执行出厂敏感词与 Prompt 注入检查...")
    dfa = DFAFilter(os.path.join(PROJECT_ROOT, "config", "sensitive_words.txt"))
    for scene in mock_data.get("scenes", []):
        is_hit, word = dfa.contains_sensitive(scene.get("segment_narration", ""))
        if is_hit:
            raise ValueError(f"出厂审查未通过，台词命中敏感词: {word}")
        if scene.get("code_snippet"):
            is_hit_c, word_c = dfa.contains_sensitive(scene["code_snippet"])
            if is_hit_c:
                raise ValueError(f"出厂审查未通过，代码片段命中敏感词: {word_c}")
    print("✅ 敏感词与上游内容出厂审查 100% 绿灯通过！")

    draft_payload = DraftScriptPayload(**mock_data)
    db.create_task(
        task_id=task_id,
        source_url="https://github.com/microsoft/markitdown",
        entity_key=draft_payload.entity_key,
        theme=draft_payload.theme,
        target_account_id="douyin-tech-main",
        draft_script=mock_data
    )

    # 3. 两阶段音画同步与精确时间轴生成 (TTS Aligner)
    print("\n[Step 3/5] 执行 Edge-TTS 音频合成与句级字幕对齐...")
    aligner = TTSAligner(voice="zh-CN-YunxiNeural", rate="+10%")
    aligned_payload = await aligner.align_draft(
        draft=draft_payload,
        task_id=task_id,
        output_base_dir=renderer_public_dir,
        template_override=template if template != "auto" else None
    )

    task_json_path = os.path.join(renderer_public_dir, "tasks", task_id, "aligned_render.json")
    with open(task_json_path, "w", encoding="utf-8") as f:
        json.dump(aligned_payload.model_dump(), f, ensure_ascii=False, indent=2)

    total_sec = aligned_payload.total_duration_in_frames / aligned_payload.fps
    print(f"✅ 音频合成与时间轴对齐完毕！总帧数: {aligned_payload.total_duration_in_frames} 帧 (约 {total_sec:.2f} 秒)")
    db.update_task_status(task_id, "TTS_ALIGNED", aligned_render_json=json.dumps(aligned_payload.model_dump(), ensure_ascii=False))

    # 4. 调用 Remotion 渲染 9:16 1080P 短视频成片
    print("\n[Step 4/5] 启动 Remotion 无状态渲染子进程...")
    video_out_path = os.path.join(output_dir, "video.mp4")
    cover_out_path = os.path.join(output_dir, "cover.png")
    xhs_card_out_path = os.path.join(output_dir, "xhs_cards", "card_0.png")

    props_rel_path = f"./public/tasks/{task_id}/aligned_render.json"
    renderer_dir = os.path.join(PROJECT_ROOT, "renderer")

    print(f"  -> 渲染视频: {video_out_path}")
    cmd_video = [
        "npx", "remotion", "render", "src/index.ts", "GeekShortVideo", video_out_path,
        f"--props={props_rel_path}",
        "--codec=h264", "--crf=18", "--pixel-format=yuv420p"
    ]
    subprocess.check_call(cmd_video, cwd=renderer_dir)

    print(f"  -> 渲染封面: {cover_out_path}")
    cmd_cover = [
        "npx", "remotion", "still", "src/index.ts", "CoverStill", cover_out_path,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_cover, cwd=renderer_dir)

    print(f"  -> 导出小红书 3:4 极客图文卡片: {xhs_card_out_path}")
    cmd_xhs = [
        "npx", "remotion", "still", "src/index.ts", "XhsCardStill", xhs_card_out_path,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_xhs, cwd=renderer_dir)

    # 5. 更新状态机并验证成果
    print("\n[Step 5/5] 验证成果并同步状态机...")
    db.update_task_status(
        task_id,
        "RENDERED",
        video_path=video_out_path,
        cover_path=cover_out_path,
        xhs_cards_dir=os.path.join(output_dir, "xhs_cards")
    )

    video_size_mb = os.path.getsize(video_out_path) / (1024 * 1024)
    print("\n" + "=" * 60)
    print(f"🎉 Phase 1 验收通过！归档产物路径：")
    print(f"  归档目录: {output_dir}")
    print(f"  1. 抖音短视频 MP4: {video_out_path} ({video_size_mb:.2f} MB)")
    print(f"  2. 抖音封面海报:   {cover_out_path}")
    print(f"  3. 小红书 3:4 卡片: {xhs_card_out_path}")
    print("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 1 端到端合成演示")
    parser.add_argument(
        "--template",
        type=str,
        default="auto",
        help="指定渲染模板 ID (auto | editorial_news | cupertino_glass | neo_brutalism | notion_craft | linear_obsidian)",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="打印当前所有可用视觉模板并退出",
    )
    args = parser.parse_args()

    if args.list_templates:
        mgr = TemplateManager()
        enabled = mgr.list_enabled_templates()
        print("当前已启用的视觉模板列表：")
        for tid in enabled:
            meta = mgr.get_template_meta(tid)
            print(f"  - {tid}: {meta.get('name', tid)} ({meta.get('description', '')})")
        sys.exit(0)

    asyncio.run(run_phase1_demo(template=args.template))
