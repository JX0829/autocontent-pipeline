#!/usr/bin/env python3
"""
scripts/channels_live_publisher.py
使用持久化的微信视频号助手 (channels.weixin.qq.com) 会话，自动完成视频上传与公开/草稿发布
支持：
1. 自动填入标题与正文
2. 自动上传 9:16 视频原片
3. 勾选原创/AIGC 声明
4. 点击发布或保存草稿
"""

import os
import sys
import time
import argparse
import asyncio
from pathlib import Path

project_root = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline")
sys.path.insert(0, str(project_root))

from playwright.async_api import async_playwright

DATA_DIR = project_root / "data" / "browser_profiles" / "channels-tech"
STATE_PATH = DATA_DIR / "storage_state.json"
POST_URL = "https://channels.weixin.qq.com/platform/post/create"

async def publish_channels(video_path: str, cover_path: str, title: str, desc: str, tags: list, is_publish: bool = True):
    if not STATE_PATH.exists():
        print(f"ERROR: No storage_state.json found at {STATE_PATH}")
        sys.exit(1)

    print(f"🚀 Starting Playwright with storage_state: {STATE_PATH}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ]
        )
        context = await browser.new_context(
            storage_state=str(STATE_PATH),
            viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()

        print(f"Navigating to {POST_URL}...")
        await page.goto(POST_URL, wait_until="networkidle")
        await asyncio.sleep(3)

        # 检查是否成功处于发布页面
        if "login" in page.url:
            print("ERROR: Login expired! Redirected to login page.")
            await browser.close()
            sys.exit(2)

        print("Page URL:", page.url)
        # 截图排查当前页面结构
        debug_shot = DATA_DIR / "post_create_page.png"
        await page.screenshot(path=str(debug_shot))
        print(f"Debug screenshot saved to: {debug_shot}")

        # 1. 上传视频文件
        # 微信视频号助手上传文件 input[type=file]
        file_input = await page.query_selector("input[type='file']")
        if not file_input:
            print("ERROR: File input not found on create page.")
            await browser.close()
            sys.exit(3)

        print(f"Uploading video file: {video_path}...")
        await file_input.set_input_files(video_path)

        # 等待视频上传并解析完成（出现进度或描述输入框）
        print("Waiting for video upload to complete...")
        for _ in range(60):
            # 检查是否有标题/描述输入框
            desc_area = await page.query_selector("textarea, .input-editor, [contenteditable='true']")
            if desc_area:
                print("Video upload completed! Editor ready.")
                break
            await asyncio.sleep(2)

        await asyncio.sleep(3)

        # 2. 填写文案内容
        tag_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in tags[:5]])
        full_text = f"{title}\n\n{desc}\n\n{tag_str}"

        # 尝试填入描述
        editor = await page.query_selector("[contenteditable='true'], textarea")
        if editor:
            await editor.click()
            await page.keyboard.type(full_text, delay=10)
            print("Description typed successfully.")

        # 3. 勾选 AIGC / 原创声明
        # 查找包含 AIGC、人工智能 或 声明 的复选框
        labels = await page.query_selector_all("label, .ant-checkbox-wrapper, .weui-desktop-form__check-label")
        for lb in labels:
            text = await lb.inner_text()
            if any(k in text for k in ["AI", "人工智能", "AIGC", "声明"]):
                print(f"Found AIGC/Statement checkbox: {text}")
                await lb.click()
                await asyncio.sleep(1)

        # 截图保存填写完毕状态
        await page.screenshot(path=str(DATA_DIR / "post_filled.png"))

        # 4. 执行发布或存草稿
        if is_publish:
            # 查找“发表”按钮
            btn = await page.query_selector("button:has-text('发表'), button:has-text('发布'), .ant-btn-primary")
            if btn:
                print("Clicking publish button...")
                await btn.click()
                print("Publish clicked!")
        else:
            # 查找“存草稿”按钮
            btn = await page.query_selector("button:has-text('草稿'), button:has-text('保存')")
            if btn:
                print("Clicking draft button...")
                await btn.click()
                print("Draft clicked!")

        await asyncio.sleep(5)
        await page.screenshot(path=str(DATA_DIR / "post_final.png"))
        await browser.close()
        print("SUCCESS: Post process finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--cover", required=False, default="")
    parser.add_argument("--title", required=True)
    parser.add_argument("--desc", required=True)
    parser.add_argument("--tags", required=True)
    parser.add_argument("--publish", action="store_true", default=False)
    args = parser.parse_args()

    tag_list = args.tags.split(",") if "," in args.tags else [args.tags]
    asyncio.run(publish_channels(
        video_path=args.video,
        cover_path=args.cover,
        title=args.title,
        desc=args.desc,
        tags=tag_list,
        is_publish=args.publish
    ))
