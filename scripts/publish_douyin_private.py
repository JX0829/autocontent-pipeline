#!/usr/bin/env python3
"""
scripts/publish_douyin_private.py
抖音【仅自己可见】私密发布统一入口（带短信验证码人机协同）

流程：
1. 上传视频 -> 填写标题/话题/正文 -> 强制勾选「仅自己可见」-> 点击发布
2. 若弹出「接收短信验证码」风控弹窗：自动点击【获取验证码】，输出弹窗截图，等待验证码
   （验证码写入 /tmp/douyin_code.txt 后由本脚本自动填入并点击【验证】）
3. 发布后必须回读内容管理列表，标题真实命中才算成功（拒绝虚报）

用法：
  python3 scripts/publish_douyin_private.py --task-id <task_id>
  echo 123456 > /tmp/douyin_code.txt     # 收到短信后写入验证码
"""

import os
import sys
import time
import json
import argparse
import sqlite3
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINE_ROOT))

import yaml
from playwright.sync_api import sync_playwright
from src.providers.douyin import DouyinProvider

CODE_FILE = Path("/tmp/douyin_code.txt")
DIAG_DIR = PIPELINE_ROOT / "output" / "douyin_diag"


def load_task(task_id: str) -> dict:
    conn = sqlite3.connect(str(PIPELINE_ROOT / "data" / "pipeline_state.db"))
    row = conn.execute(
        "SELECT draft_script_json, video_path, cover_path FROM content_tasks WHERE task_id=?",
        (task_id,)
    ).fetchone()
    if not row:
        raise SystemExit(f"task not found: {task_id}")
    d = json.loads(row[0] or "{}")
    return {
        "task_id": task_id,
        "title": d.get("title", ""),
        "tags": d.get("tags") or d.get("topics") or [],
        "content": d.get("content_text") or d.get("fact_summary_150") or "",
        "video": row[1],
        "cover": row[2],
    }


def sanitize_for_douyin(t: dict) -> dict:
    """跨平台引流清洗：抖音侧不出现其它平台名"""
    t["title"] = t["title"].replace("B站", "").strip()
    t["tags"] = [
        x.replace("B站热点", "科技热点").replace("B站横屏", "科技旗舰").replace("B站", "科技")
        for x in t["tags"]
    ]
    t["content"] = (t["content"]
                    .replace("B站相关视频", "相关视频")
                    .replace("B站", "全网技术圈"))
    return t


RESEND_FLAG = Path("/tmp/douyin_resend.flag")


def wait_for_code(page, timeout_sec: int = 300) -> str:
    """等待用户把 6 位验证码写入 /tmp/douyin_code.txt；期间可通过 /tmp/douyin_resend.flag 触发重发短信"""
    deadline = time.time() + timeout_sec
    if CODE_FILE.exists():
        CODE_FILE.unlink()
    resend_count = 0
    while time.time() < deadline:
        if CODE_FILE.exists():
            code = CODE_FILE.read_text().strip()
            if code.isdigit() and len(code) >= 4:
                return code
        if RESEND_FLAG.exists():
            RESEND_FLAG.unlink()
            try:
                btn = page.locator('text=获取验证码, text=重新获取').first
                if btn.count() > 0:
                    btn.click()
                    resend_count += 1
                    print(f"CODE_RESENT=True ({resend_count})", flush=True)
            except Exception as e:
                print(f"CODE_RESEND_FAILED: {e}", flush=True)
        time.sleep(2)
    return ""


def run(task_id: str, code_timeout: int = 300) -> dict:
    task = sanitize_for_douyin(load_task(task_id))
    video, cover = task["video"], task["cover"]
    if not video or not os.path.exists(video):
        return {"success": False, "error": "VIDEO_NOT_FOUND", "path": video}

    acc = [a for a in yaml.safe_load(open(PIPELINE_ROOT / "config" / "accounts.yaml"))["accounts"]
           if a["id"] == "douyin-tech-main"][0]
    provider = DouyinProvider(acc)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    topic_tags = " ".join([f"#{t.strip('#')}" for t in task["tags"]])
    caption = f"{task['title']} {topic_tags}\n{task['content']}".strip()

    with sync_playwright() as p:
        context, browser = provider._get_browser_context(p, headless=True)
        page = context.new_page()
        page.goto(provider.CREATOR_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)
        page.goto(provider.UPLOAD_URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)

        # 上传
        upload_input = page.locator('input[type="file"]').first
        if upload_input.count() > 0:
            upload_input.set_input_files(os.path.abspath(video))
        else:
            with page.expect_file_chooser(timeout=15000) as fc:
                page.locator("text=点击上传, .semi-upload-drag-area").first.click()
            fc.value.set_files(os.path.abspath(video))

        page.wait_for_selector('div[contenteditable="true"], textarea', timeout=90000)
        time.sleep(4)

        # 等视频真正上传完成（最多 180s）
        uploaded = False
        for _ in range(60):
            body = page.inner_text("body")
            if "上传成功" in body or "重新上传" in body:
                uploaded = True
                break
            time.sleep(3)
        print(f"UPLOAD_DONE={uploaded}", flush=True)

        # 填文案
        editor = page.locator('div[contenteditable="true"]').first
        if editor.count() > 0:
            editor.click()
            editor.fill("")
            editor.type(caption, delay=30)
        else:
            page.locator("textarea").first.fill(caption)
        time.sleep(2)

        # 强制私密
        private_label = page.locator('label:has-text("仅自己可见")').first
        if private_label.count() == 0:
            private_label = page.locator('xpath=//label[contains(., "仅自己可见")]').first
        if private_label.count() == 0:
            browser.close()
            return {"success": False, "error": "PRIVATE_LABEL_NOT_FOUND"}
        private_label.click()
        time.sleep(2)
        print("PRIVATE_SELECTED=True", flush=True)

        # 发布
        btn = None
        for b in page.locator('button:has-text("发布")').all():
            try:
                if b.inner_text().strip() == "发布" and b.is_visible() and b.is_enabled():
                    btn = b
                    break
            except Exception:
                continue
        if btn is None:
            browser.close()
            return {"success": False, "error": "PUBLISH_BUTTON_NOT_FOUND"}
        btn.click()
        time.sleep(4)

        # 风控验证码弹窗
        body = page.inner_text("body")
        need_code = ("接收短信验证码" in body) or ("短信验证码" in body) or ("获取验证码" in body)
        code_used = ""
        if need_code:
            page.screenshot(path=str(DIAG_DIR / f"code_modal_{ts}.png"), full_page=True)
            print("SMS_CODE_REQUIRED=True", flush=True)
            try:
                get_btn = page.locator('text=获取验证码').first
                if get_btn.count() > 0:
                    get_btn.click()
                    print("CODE_REQUEST_SENT=True", flush=True)
            except Exception as e:
                print(f"CODE_REQUEST_CLICK_FAILED: {e}", flush=True)

            code_used = wait_for_code(page, code_timeout)
            if not code_used:
                page.screenshot(path=str(DIAG_DIR / f"code_timeout_{ts}.png"), full_page=True)
                browser.close()
                return {"success": False, "error": "CODE_TIMEOUT",
                        "message": f"等待验证码超时（{code_timeout}s）"}
            print(f"CODE_RECEIVED={code_used}", flush=True)
            try:
                inputs = page.locator('input[type="text"], input[type="tel"], input[inputmode="numeric"]').all()
                filled = False
                for inp in inputs:
                    if inp.is_visible():
                        inp.click()
                        inp.type(code_used, delay=80)
                        filled = True
                        break
                if not filled:
                    page.keyboard.type(code_used, delay=80)
                time.sleep(1)
                verify_btn = page.locator('button:has-text("验证"), text=验证').first
                if verify_btn.count() > 0:
                    verify_btn.click()
                time.sleep(6)
            except Exception as e:
                print(f"CODE_FILL_FAILED: {e}", flush=True)

        # 等发布信号
        signal = None
        for _ in range(45):
            time.sleep(2)
            if "creator-micro/content/manage" in page.url or "creator-micro/home" in page.url:
                signal = "url_redirect"
                break
            try:
                btxt = page.inner_text("body")
            except Exception:
                btxt = ""
            if "发布成功" in btxt:
                signal = "toast"
                break
            if "验证码" in btxt and code_used:
                continue
        print(f"PUBLISH_SIGNAL={signal}", flush=True)

        page.screenshot(path=str(DIAG_DIR / f"after_publish_{ts}.png"), full_page=True)
        browser.close()

    verified = provider._verify_published(task["title"])
    ok = bool(verified.get("found"))
    return {
        "success": ok,
        "task_id": task_id,
        "title": task["title"],
        "signal": signal,
        "code_used": bool(code_used),
        "readback": verified,
        "message": "已回读确认私密作品存在" if ok else "回读未命中，判定未发布",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--code-timeout", type=int, default=300)
    a = ap.parse_args()
    res = run(a.task_id, a.code_timeout)
    print("FINAL_RESULT:", json.dumps(res, ensure_ascii=False), flush=True)
