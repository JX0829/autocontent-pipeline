import asyncio
import os
import sys
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path('/Users/a1/Downloads/workspace/team/project/autocontent-pipeline')
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.render_hotspot import render_hotspot_video
from src.db import PipelineDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("RenderAndDispatch")

async def main(task_id: str):
    draft_path = PROJECT_ROOT / "data" / f"draft_{task_id}.json"
    if not draft_path.exists():
        logger.error(f"Draft file not found: {draft_path}")
        sys.exit(1)

    logger.info(f"Rendering video for task {task_id}...")
    video_path, cover_path, output_dir, duration, resolved_tpl = await render_hotspot_video(str(draft_path), task_id)
    logger.info(f"Render complete! Video: {video_path}")

    # 更新数据库状态为 APPROVED
    db_path = PROJECT_ROOT / "data" / "pipeline_state.db"
    db = PipelineDB(str(db_path))
    db.update_task_status(task_id, "APPROVED")
    logger.info(f"Task {task_id} set to APPROVED. Starting publish worker...")

    # 执行发布分发
    import subprocess
    publish_script = PROJECT_ROOT / "scripts" / "run_publish_worker.py"
    res = subprocess.run([sys.executable, str(publish_script), "--task-id", task_id], capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    if res.returncode != 0:
        logger.error(f"Publish worker failed with code {res.returncode}")
        sys.exit(res.returncode)

    logger.info("All done successfully!")

if __name__ == "__main__":
    task_id = sys.argv[1] if len(sys.argv) > 1 else "news_yc_agents_1793613093"
    asyncio.run(main(task_id))
