import os
import sys
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.feishu.approval_service import FeishuApprovalService

TASK_ID = "news_gvs5h_20261025_150348"
RENDERER_DIR = PROJECT_ROOT / "renderer"
TASK_JSON = RENDERER_DIR / "public" / "tasks" / TASK_ID / "aligned_render.json"
OUTPUT_BASE = PROJECT_ROOT / "output" / "template_showcase" / TASK_ID
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

TEMPLATES = [
    ("editorial_news", "T1 · 瑞士先锋报刊风 (Editorial Paper)"),
    ("cupertino_glass", "T2 · Apple 毛玻璃流光风 (Cupertino Glass)"),
    ("neo_brutalism", "T3 · 多巴胺波普粗野风 (Neo-Brutalism Pop)"),
    ("notion_craft", "T4 · Notion 暖调手账风 (Notion Warm Craft)"),
    ("linear_obsidian", "T5 · Linear 钛黑流光风 (Linear Obsidian)"),
]

def render_template(tpl_id: str, tpl_name: str):
    print(f"\n==================================================")
    print(f"🎬 开始渲染模板: {tpl_id} - {tpl_name}")
    print(f"==================================================")
    
    # 动态更新 aligned_render.json 中的 template_id
    with open(TASK_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["template_id"] = tpl_id
    with open(TASK_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tpl_dir = OUTPUT_BASE / tpl_id
    tpl_dir.mkdir(parents=True, exist_ok=True)
    video_path = tpl_dir / f"video_{tpl_id}.mp4"
    cover_path = tpl_dir / f"cover_{tpl_id}.png"

    props_rel = f"./public/tasks/{TASK_ID}/aligned_render.json"

    # 1. 渲染视频
    print(f"  -> 渲染视频: {video_path}")
    cmd_video = [
        "npx", "remotion", "render", "src/index.ts", "GeekShortVideo", str(video_path),
        f"--props={props_rel}", "--codec=h264", "--crf=18", "--pixel-format=yuv420p"
    ]
    subprocess.run(cmd_video, cwd=str(RENDERER_DIR), check=True)

    # 2. 渲染封面
    print(f"  -> 渲染封面: {cover_path}")
    cmd_cover = [
        "npx", "remotion", "still", "src/index.ts", "CoverStill", str(cover_path),
        f"--props={props_rel}", "--image-format=png"
    ]
    subprocess.run(cmd_cover, cwd=str(RENDERER_DIR), check=True)

    return str(video_path), str(cover_path)

if __name__ == "__main__":
    for tpl_id, tpl_name in TEMPLATES:
        render_template(tpl_id, tpl_name)
    print("\n✅ 所有 5 套模板全部渲染完毕！")
