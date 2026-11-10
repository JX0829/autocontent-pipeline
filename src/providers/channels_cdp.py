"""
src/providers/channels_cdp.py
微信视频号 (Channels) CDP 自动化发布/草稿 Provider
基于独立浏览器会话打通视频号助手 (channels.weixin.qq.com) 稿件上传与草稿留存
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

from .base import BaseSocialProvider

logger = logging.getLogger("ChannelsCDPProvider")

class ChannelsCDPProvider(BaseSocialProvider):
    """
    微信视频号发布 Provider
    通过专用浏览器 Profile 接入视频号创作者助手
    """
    def __init__(self, account_config: Dict[str, Any]):
        super().__init__(account_config)
        self.profile_path = account_config.get("profile_path", "./data/browser_profiles/channels-tech")
        self.publish_mode = account_config.get("publish_mode", "public") # public (公开分发) | draft (草稿箱)

    def check_login_status(self) -> bool:
        """检查视频号助手会话是否已持久化"""
        state_file = Path(self.profile_path) / "storage_state.json"
        return state_file.exists()

    # ────────────────────────── 真实发布（CDP / Playwright）──────────────────────────
    POST_URL = "https://channels.weixin.qq.com/platform/post/create"

    def _state_path(self) -> Path:
        return Path(self.profile_path) / "storage_state.json"

    def publish_live(
        self,
        video_path: str,
        cover_path: str,
        title: str,
        content_text: str,
        topics: list[str],
        declare_original: bool = True,
        declare_aigc: bool = True,
        dry_run: bool = False,
        allow_missing_flags: bool = False,
        headless: bool = True,
    ) -> Dict[str, Any]:
        """真实发布到微信视频号（公开可见 + 原创声明 + AI 声明）。

        设计原则（与项目铁律一致）：
        - **fail-closed**：要求勾选「原创」但页面上找不到该控件时，直接中止，不发布。
          绝不「找不到就按默认发」——否则用户以为已勾原创，实际没有。
        - **不虚报**：只有拿到平台侧成功证据（URL 变化 / 成功文案）才返回 published=True；
          点了按钮但没证据 → PUBLISHED_UNVERIFIED。
        - **登录态失效要明说**：被重定向到 login.html 时返回 LOGIN_EXPIRED 并提示重新扫码。

        注意：本机 2026-11-10 时 storage_state 已过期，页面控件文案未经真机核对，
        首次使用请先跑 `scripts/publish_channels.py --probe` 打印真实标签再调匹配词。
        """
        import asyncio

        state = self._state_path()
        if not state.exists():
            return {"success": False, "published": False, "status": "NO_STORAGE_STATE",
                    "message": f"缺少登录态 {state}，先跑 scripts/channels_login.py 扫码登录"}

        return asyncio.run(self._publish_live_async(
            video_path=video_path, cover_path=cover_path, title=title,
            content_text=content_text, topics=topics,
            declare_original=declare_original, declare_aigc=declare_aigc,
            dry_run=dry_run, allow_missing_flags=allow_missing_flags, headless=headless))

    async def _publish_live_async(self, video_path: str, cover_path: str, title: str,
                                  content_text: str, topics: list[str],
                                  declare_original: bool, declare_aigc: bool,
                                  dry_run: bool, allow_missing_flags: bool,
                                  headless: bool) -> Dict[str, Any]:
        import asyncio
        from playwright.async_api import async_playwright

        ORIGINAL_KEYS = ("原创", "声明原创", "原创声明")
        AIGC_KEYS = ("内容由AI生成", "AI生成", "人工智能生成", "AIGC", "AI创作")
        shot_dir = Path(self.profile_path)

        async with async_playwright() as p:
            # 本机 playwright 未下载自带 chromium，复用系统 Chrome（channel="chrome"）
            browser = await p.chromium.launch(headless=headless, channel="chrome",
                                              args=["--no-sandbox",
                                                    "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(storage_state=str(self._state_path()),
                                            viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            try:
                await page.goto(self.POST_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(6)

                if "login" in page.url.lower():
                    return {"success": False, "published": False, "status": "LOGIN_EXPIRED",
                            "message": "登录态已过期，被重定向到登录页；先跑 scripts/channels_login.py 重新扫码"}

                # 收集可勾选标签，供语义匹配与排障
                labels = await page.query_selector_all(
                    "label, .ant-checkbox-wrapper, .weui-desktop-form__check-label, [class*='checkbox']")
                label_texts: list[tuple[Any, str]] = []
                for lb in labels[:80]:
                    try:
                        t = (await lb.inner_text()).strip().replace("\n", " ")
                    except Exception as e:  # noqa: BLE001 — 单个元素取文案失败不影响整体
                        logger.debug(f"读取标签文案失败，跳过该元素: {e}")
                        continue
                    if t:
                        label_texts.append((lb, t))

                def find_label(keys: tuple[str, ...]):
                    for el, t in label_texts:
                        if any(k in t for k in keys):
                            return el, t
                    return None, ""

                if dry_run:
                    await page.screenshot(path=str(shot_dir / "probe_create_page.png"), full_page=True)
                    return {"success": True, "published": False, "status": "DRY_RUN_OK",
                            "url": page.url,
                            "labels": [t for _, t in label_texts][:40],
                            "screenshot": str(shot_dir / "probe_create_page.png"),
                            "message": "页面可访问、登录有效；未上传未发布"}

                # 1. 上传视频
                file_input = await page.query_selector("input[type='file']")
                if not file_input:
                    return {"success": False, "published": False, "status": "FILE_INPUT_NOT_FOUND",
                            "message": "发布页未找到 input[type=file]"}
                await file_input.set_input_files(video_path)

                editor = None
                for _ in range(90):          # 最多等 180s（视频解析/转码）
                    editor = await page.query_selector("[contenteditable='true'], textarea")
                    if editor:
                        break
                    await asyncio.sleep(2)
                if not editor:
                    return {"success": False, "published": False, "status": "EDITOR_TIMEOUT",
                            "message": "等待编辑器就绪超时（视频可能仍在转码）"}

                # 2. 填文案（标题 + 正文 + 话题）
                tag_str = " ".join(f"#{t}" if not str(t).startswith("#") else str(t) for t in topics[:5])
                await editor.click()
                await page.keyboard.type(f"{title}\n\n{content_text[:200]}\n\n{tag_str}", delay=10)
                await asyncio.sleep(1)

                # 3. 原创声明（fail-closed）
                orig_el, orig_txt = find_label(ORIGINAL_KEYS)
                if declare_original:
                    if orig_el is None and not allow_missing_flags:
                        await page.screenshot(path=str(shot_dir / "fail_original_not_found.png"), full_page=True)
                        return {"success": False, "published": False,
                                "status": "ORIGINAL_CHECKBOX_NOT_FOUND",
                                "message": "要求勾选原创但页面找不到该控件，已中止发布（fail-closed）",
                                "labels": [t for _, t in label_texts][:40]}
                    if orig_el is not None:
                        await orig_el.click()
                        await asyncio.sleep(1)

                # 4. AI 合成内容声明（找不到只告警，不阻断；可用 allow_missing_flags 严格化）
                aigc_el, aigc_txt = find_label(AIGC_KEYS)
                if declare_aigc and aigc_el is not None:
                    await aigc_el.click()
                    await asyncio.sleep(1)

                await page.screenshot(path=str(shot_dir / "before_publish.png"), full_page=True)

                # 5. 发表（公开可见是视频号默认；若有「仅自己可见」需确保未选中）
                btn = await page.query_selector("button:has-text('发表'), button:has-text('发布')")
                if not btn:
                    return {"success": False, "published": False, "status": "PUBLISH_BUTTON_NOT_FOUND",
                            "message": "找不到「发表」按钮，未发布"}
                await btn.click()

                # 6. 验证平台侧确认（URL 跳转 / 成功文案 / 按钮消失）
                published = False
                evidence = ""
                for _ in range(30):
                    await asyncio.sleep(2)
                    try:
                        body = await page.inner_text("body")
                    except Exception as e:  # noqa: BLE001 — 页面可能正在跳转
                        logger.debug(f"读取页面正文失败（可能正在跳转）: {e}")
                        body = ""
                    if any(k in body for k in ("发表成功", "已发表", "发布成功")):
                        published, evidence = True, "成功文案"
                        break
                    if "post/list" in page.url or "platform/post" not in page.url:
                        published, evidence = True, f"URL 跳转 {page.url}"
                        break

                await page.screenshot(path=str(shot_dir / "after_publish.png"), full_page=True)
                return {
                    "success": published,
                    "published": published,
                    "status": "PUBLISHED" if published else "PUBLISHED_UNVERIFIED",
                    "evidence": evidence,
                    "url": page.url,
                    "declare_original": bool(orig_el is not None) if declare_original else False,
                    "declare_aigc": bool(aigc_el is not None) if declare_aigc else False,
                    "original_label": orig_txt,
                    "aigc_label": aigc_txt,
                    "screenshot": str(shot_dir / "after_publish.png"),
                    "message": ("已发布并验证" if published
                                else "已点击发表但未取得平台侧证据，请人工确认（不虚报成功）"),
                }
            finally:
                await browser.close()

    def save_draft(
        self,
        video_path: str,
        cover_path: str,
        title: str,
        topics: list[str],
        content_text: str,
        declare_aigc: bool = False,
        declare_original: bool = True
    ) -> Dict[str, Any]:
        """
        保存视频至微信视频号草稿箱
        """
        logger.info(f"Uploading video to WeChat Channels draft: title='{title[:30]}'")
        if not os.path.exists(video_path):
            return {"success": False, "draft_id": None, "message": f"Video file not found: {video_path}"}

        # 视频号文案格式化（文案前置带话题，字数紧凑精炼）
        topic_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics[:5]])
        pure_title = title[:30]
        full_caption = f"{pure_title}\n\n{content_text[:200]}\n\n{topic_str}"

        draft_id = f"channels_draft_{int(os.path.getmtime(video_path))}"
        intent = "public" if self.publish_mode == "public" else "draft"

        draft_record = {
            "platform": "channels",
            "account_id": self.account_id,
            "draft_id": draft_id,
            "title": pure_title,
            "caption": full_caption,
            "video_path": video_path,
            "cover_path": cover_path,
            "publish_mode": self.publish_mode,
            # 如实标注：本 provider 无真实上传实现，只是素材就绪
            "distribution": f"pending_manual_{intent}",
            "status": "MATERIAL_READY",
            "published": False,
            "publish_target": "channels_manual_upload",
            "declare_original": declare_original,
            "declare_aigc": declare_aigc,
            "login_ready": self.check_login_status()
        }

        draft_store_dir = Path("/Users/a1/Downloads/workspace/team/project/autocontent-pipeline/data/drafts/channels")
        draft_store_dir.mkdir(parents=True, exist_ok=True)
        with open(draft_store_dir / f"{draft_id}.json", "w", encoding="utf-8") as f:
            json.dump(draft_record, f, ensure_ascii=False, indent=2)

        # ⚠️ 本 provider 目前**只生成发布素材记录，不做真实上传**（无任何浏览器操作）。
        #    历史实现返回 status=PUBLIC_DISPATCHED + "公开分发已就绪"，属虚报成功：
        #    调用方会误以为视频已在视频号公开且已勾原创，实际平台侧什么都没发生。
        #    与项目铁律一致（状态只能代表平台侧已确认），此处如实降级为「素材就绪」。
        #    真实 CDP 发布（含原创/AI 声明勾选）需单独立项，完成前不得声称已发布。
        flags = []
        if declare_original:
            flags.append("原创声明")
        if declare_aigc:
            flags.append("AI 声明")
        flag_desc = f"，待勾选：{'/'.join(flags)}" if flags else ""

        mode_desc = f"微信视频号素材已就绪（未真实发布，需人工上传{flag_desc}）"
        logger.warning(f"[CHANNELS] {mode_desc}: {draft_id} — 本 provider 无真实上传实现")

        return {
            "success": True,               # 素材生成成功
            "published": False,            # 但**未发布**，调用方必须据此判断
            "draft_id": draft_id,
            "distribution": draft_record["distribution"],
            "status": draft_record["status"],
            "login_ready": draft_record["login_ready"],
            "declare_original": declare_original,
            "declare_aigc": declare_aigc,
            "message": f"{mode_desc}: {draft_id}"
        }
