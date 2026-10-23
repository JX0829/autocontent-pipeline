#!/usr/bin/env python3
"""
scripts/login_douyin.py
抖音创作者中心账号登录辅助脚本
启动有头 Chromium 浏览器，加载指定账号的 profile 目录，由人工完成扫码或短信登录。
登录完成后自动将 Cookie 与 storage_state 持久化保存至独立目录。
"""

import os
import sys
import argparse
import yaml
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
if str(PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINE_ROOT))

from playwright.sync_api import sync_playwright

DOUYIN_CREATOR_URL = "https://creator.douyin.com/"


def load_account_config(account_id: str):
    config_path = PIPELINE_ROOT / "config" / "accounts.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    accounts = data.get("accounts", [])
    for acc in accounts:
        if acc.get("id") == account_id:
            return acc
    raise ValueError(f"Account '{account_id}' not found in accounts.yaml")


def login_and_save_state(account_id: str):
    account = load_account_config(account_id)
    profile_rel = account.get("profile_path", f"./data/browser_profiles/{account_id}")
    profile_dir = (PIPELINE_ROOT / profile_rel).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    state_file = profile_dir / "storage_state.json"

    print(f"\n==================================================")
    print(f"🚀 启动抖音登录会话: {account.get('name')} ({account_id})")
    print(f"📁 Profile 存储路径: {profile_dir}")
    print(f"==================================================")

    with sync_playwright() as p:
        # 使用持久化上下文 launch_persistent_context
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled"]
        )

        page = context.new_page() if not context.pages else context.pages[0]
        print(f"正在打开抖音创作者中心: {DOUYIN_CREATOR_URL}")
        page.goto(DOUYIN_CREATOR_URL)

        print("\n👉 请在弹出的浏览器窗口中完成【扫码登录】或【手机号验证码登录】。")
        print("💡 登录成功并进入创作者主页后，在此终端按下回车键保存登录状态...")

        try:
            input()
        except EOFError:
            # 非交互终端等待页面跳转
            print("等待检测创作者中心登录跳转...")
            page.wait_for_timeout(10000)

        # 导出并持久化 storage_state
        context.storage_state(path=str(state_file))
        print(f"\n✅ 登录凭据已成功持久化至: {state_file}")
        context.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抖音账号登录辅助工具")
    parser.add_argument("--account", default="douyin-tech-main", help="账号 ID (默认: douyin-tech-main)")
    args = parser.parse_args()
    login_and_save_state(args.account)
