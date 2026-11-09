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
from src.utils.accounts import default_account

def _require_source_url(data: dict) -> str:
    """T4.2：物料必须自带 source_url，禁止代码里编造占位来源。"""
    url = str(data.get("source_url") or (data.get("manifest") or {}).get("source_url") or "")
    if not url:
        raise ValueError("缺 source_url：物料未提供，禁止用占位 URL 兜底")
    return url


from src.safety.dfa_filter import DFAFilter
from src.tts.aligner import TTSAligner

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

async def render_hotspot_video(script_path: str, task_id: str, template: str = "auto"):
    print("=" * 60)
    print(f"🚀 启动热点短视频全流程合成: {task_id}")
    print("=" * 60)

    renderer_public_dir = os.path.join(PROJECT_ROOT, "renderer", "public")
    now = datetime.now()
    date_folder = now.strftime("%Y%m%d")
    time_prefix = now.strftime("%H%M%S")
    output_dir = os.path.join(PROJECT_ROOT, "output", date_folder, f"{time_prefix}_{task_id}")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(os.path.join(output_dir, "xhs_cards"), exist_ok=True)

    db = PipelineDB(os.path.join(PROJECT_ROOT, "data", "pipeline_state.db"))
    with open(script_path, "r", encoding="utf-8") as f:
        script_data = json.load(f)

    print("\n[1/4] 执行出厂敏感词与合规门禁检查...")
    dfa = DFAFilter(os.path.join(PROJECT_ROOT, "config", "sensitive_words.txt"))
    scenes = script_data.get("scenes", [])
    for idx, scene in enumerate(scenes):
        is_hit, word = dfa.contains_sensitive(scene.get("segment_narration", ""))
        if is_hit:
            raise ValueError(f"台词命中敏感词: {word}")
        if scene.get("code_snippet"):
            is_hit_c, word_c = dfa.contains_sensitive(scene["code_snippet"])
            if is_hit_c:
                raise ValueError(f"代码片段命中敏感词: {word_c}")
        # 强制检查尾部分镜：严禁导流词，尾部分镜必须是合规结尾
        if idx == len(scenes) - 1:
            for forbidden_word in ["暗号", "私信", "主页置顶", "粉丝群", "领取", "求源码", "领源码", "加群"]:
                if forbidden_word in scene.get("segment_narration", ""):
                    raise ValueError(f"结尾台词违规命中引流话术: '{forbidden_word}'！结尾必须使用纯净的'欢迎在评论区交流讨论～'")
    print("✅ 敏感词与结尾合规审查 100% 通过！")

    draft_payload = DraftScriptPayload(**script_data)
    db.create_task(
        task_id=task_id,
        source_url=_require_source_url(script_data),
        entity_key=draft_payload.entity_key,
        theme=draft_payload.theme,
        target_account_id=str(script_data.get("account") or default_account()),
        draft_script=script_data
    )

    print("\n[2/4] 执行 Edge-TTS 音频合成与字幕毫秒对齐...")
    aligner = TTSAligner.from_slot("tts.tech")
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

    print("\n[3/4] 启动 Remotion 渲染 9:16 1080P 视频...")
    video_out_path = os.path.join(output_dir, "video.mp4")
    cover_out_path = os.path.join(output_dir, "cover.png")
    xhs_card_out_path = os.path.join(output_dir, "xhs_cards", "card_0.png")

    props_rel_path = f"./public/tasks/{task_id}/aligned_render.json"
    renderer_dir = os.path.join(PROJECT_ROOT, "renderer")

    cmd_video = [
        "npx", "remotion", "render", "src/index.ts", "GeekShortVideo", video_out_path,
        f"--props={props_rel_path}",
        "--codec=h264", "--crf=18", "--pixel-format=yuv420p"
    ]
    subprocess.check_call(cmd_video, cwd=renderer_dir)

    cmd_cover = [
        "npx", "remotion", "still", "src/index.ts", "CoverStill", cover_out_path,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_cover, cwd=renderer_dir)

    cmd_xhs = [
        "npx", "remotion", "still", "src/index.ts", "XhsCardStill", xhs_card_out_path,
        f"--props={props_rel_path}", "--image-format=png"
    ]
    subprocess.check_call(cmd_xhs, cwd=renderer_dir)

    db.update_task_status(
        task_id,
        "RENDERED",
        video_path=video_out_path,
        cover_path=cover_out_path,
        xhs_cards_dir=os.path.join(output_dir, "xhs_cards")
    )
    print(f"\n[4/4] 渲染完成！成片路径: {video_out_path}")
    return video_out_path, cover_out_path, output_dir, total_sec, aligned_payload.template_id

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", default=os.path.join(PROJECT_ROOT, "data", "draft_steertts.json"))
    parser.add_argument("--task-id", default=None)
    args = parser.parse_args()

    with open(args.draft, "r", encoding="utf-8") as f:
        draft_data = json.load(f)
    task_id = args.task_id or draft_data.get("task_id", "task_steertts_001")

    asyncio.run(render_hotspot_video(args.draft, task_id))
