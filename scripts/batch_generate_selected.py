import asyncio
import os
import sys
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

from scripts.auto_generate_from_digest import trigger_video_from_digest
from src.db import PipelineDB

def clean_remotion_cache():
    cache_dir = PROJECT_ROOT / "renderer" / ".cache"
    remotion_dir = PROJECT_ROOT / "renderer" / ".remotion"
    remotion_root = PROJECT_ROOT / ".remotion"
    for p in [cache_dir, remotion_dir, remotion_root]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            print(f"Purged cache: {p}")

async def process_item(item_idx: str, file_path: str):
    print(f"\n==========================================")
    print(f"🚀 开始处理热点 [{item_idx}]: {file_path}")
    print(f"==========================================")
    
    clean_remotion_cache()
    
    result = await trigger_video_from_digest(digest_file=file_path)
    if not result:
        print(f"❌ 热点 [{item_idx}] 生成失败！")
        return None
        
    task_id = result["task_id"]
    video_path = result["video_path"]
    
    # 状态机闭环更新为 WAITING_APPROVAL
    db = PipelineDB(str(PROJECT_ROOT / "data" / "pipeline_state.db"))
    db.update_task_status(task_id, "WAITING_APPROVAL")
    print(f"✅ 状态机流转为 WAITING_APPROVAL (task_id: {task_id})")
    
    # ffmpeg 抽样核验视频关键帧
    frame_check = Path(video_path).parent / "frame_check.png"
    subprocess.run(["ffmpeg", "-y", "-ss", "00:00:03", "-i", video_path, "-vframes", "1", str(frame_check)], capture_output=True)
    if frame_check.exists():
        print(f"📸 关键帧抽检成功: {frame_check}")
        
    print(f"🎉 热点 [{item_idx}] 短视频生成与飞书审批下发完毕: {result['title']}")
    return result

async def main():
    items = [
        ("1", "/tmp/digest_topic_1_lightpanda.txt"),
        ("3", "/tmp/digest_topic_3_t3code.txt")
    ]
    results = []
    for idx, path in items:
        res = await process_item(idx, path)
        results.append((idx, res))
        await asyncio.sleep(2)
        
    print("\n================ 批量出片汇总 ================")
    for idx, res in results:
        if res:
            print(f"[{idx}] 成功: {res['title']} -> {res['video_path']}")
        else:
            print(f"[{idx}] 失败")

if __name__ == "__main__":
    asyncio.run(main())
