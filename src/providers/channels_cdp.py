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
    # 实测（2026-11-10）：直接访问 /platform/post/create 会被重定向回 /platform 首页，
    # 必须先进首页再点「发表视频」按钮才会真正进入发布页。这是视频号前端的硬约束。
    HOME_URL = "https://channels.weixin.qq.com/platform"
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
        stop_before_publish: bool = False,
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
            dry_run=dry_run, allow_missing_flags=allow_missing_flags, headless=headless,
            stop_before_publish=stop_before_publish))

    async def _publish_live_async(self, video_path: str, cover_path: str, title: str,
                                  content_text: str, topics: list[str],
                                  declare_original: bool, declare_aigc: bool,
                                  dry_run: bool, allow_missing_flags: bool,
                                  headless: bool, stop_before_publish: bool) -> Dict[str, Any]:
        """真实发布到视频号。2026-11-10 真机实测得出的三条硬约束：

        1. 直接访问 /platform/post/create 会跳回首页，必须先点首页「发表视频」；
        2. 发布表单在 wujie 微前端 iframe（micro/content/post/create）里，
           且该 iframe 的 document 被 wujie 代理过 —— **Playwright 的
           query_selector / locator 在它里面一律返回 0 个元素**，
           只有 frame.evaluate 能看到真实 DOM。因此本方法所有交互都走 evaluate，
           上传则用「JS 点隐藏 input → expect_file_chooser 接管」绕开选择器；
        3. 原创是 label 含「原创标记」的 checkbox；视频号 Web 端**没有** AI 声明控件。
        """
        import asyncio
        import re

        from playwright.async_api import async_playwright

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
                await page.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)

                if "login" in page.url.lower():
                    return {"success": False, "published": False, "status": "LOGIN_EXPIRED",
                            "message": "登录态已过期，被重定向到登录页；先跑 scripts/channels_login.py 重新扫码"}

                # 进发布页：首页点「发表视频」（直接访问 post/create 会被跳回）
                if "/post/create" not in page.url:
                    entry = await page.query_selector("button:has-text('发表视频')")
                    if not entry:
                        return {"success": False, "published": False, "status": "CREATE_ENTRY_NOT_FOUND",
                                "message": "首页找不到「发表视频」入口，无法进入发布页"}
                    await entry.click()
                    for _ in range(15):
                        await asyncio.sleep(1)
                        if "/post/create" in page.url:
                            break
                    if "/post/create" not in page.url:
                        return {"success": False, "published": False, "status": "CREATE_PAGE_TIMEOUT",
                                "message": f"点击「发表视频」后未进入发布页，当前 URL={page.url}"}

                # 等 wujie iframe 就绪（用 evaluate 探测，不能用 query_selector）
                ui = None
                for _ in range(25):
                    await asyncio.sleep(1)
                    ui = page.frame(url=re.compile(r"micro/content/post/create"))
                    if ui:
                        try:
                            if await ui.evaluate(
                                    "!!document.querySelector(\"input[type='file']\")"):
                                break
                        except Exception as e:  # noqa: BLE001 — iframe 可能正在重建
                            logger.debug(f"发布 iframe 尚未就绪: {e}")
                if not ui:
                    return {"success": False, "published": False, "status": "IFRAME_NOT_FOUND",
                            "message": "未找到 wujie 发布 iframe（micro/content/post/create）"}

                if dry_run:
                    await page.screenshot(path=str(shot_dir / "probe_create_page.png"), full_page=True)
                    return {"success": True, "published": False, "status": "DRY_RUN_OK",
                            "url": ui.url,
                            "screenshot": str(shot_dir / "probe_create_page.png"),
                            "message": "发布 iframe 可访问、登录有效；未上传未发布"}

                # 1. 上传：JS 点隐藏 input → 接管原生文件选择器
                uploaded = False
                for attempt in range(4):
                    try:
                        async with page.expect_file_chooser(timeout=20000) as fc:
                            await ui.evaluate(
                                "document.querySelector(\"input[type='file']\").click()")
                        chooser = await fc.value
                        await chooser.set_files(video_path)
                        uploaded = True
                        break
                    except Exception as e:  # noqa: BLE001 — 页面重渲染，重试
                        logger.debug(f"上传第 {attempt+1} 次失败: {e}")
                        await asyncio.sleep(3)
                if not uploaded:
                    return {"success": False, "published": False, "status": "UPLOAD_FAILED",
                            "message": "多次重试仍无法通过文件选择器上传视频"}

                # 2. 等编辑器就绪（视频解析/转码，最多 180s）
                editor_ready = False
                for _ in range(90):
                    try:
                        if await ui.evaluate(
                                "!!document.querySelector(\"[contenteditable='true'], textarea\")"):
                            editor_ready = True
                            break
                    except Exception as e:  # noqa: BLE001 — iframe 重建中
                        logger.debug(f"探测编辑器失败（iframe 可能重建）: {e}")
                    await asyncio.sleep(2)
                if not editor_ready:
                    return {"success": False, "published": False, "status": "EDITOR_TIMEOUT",
                            "message": "等待编辑器就绪超时（视频可能仍在转码）"}

                # 3. 填文案（标题 + 正文 + 话题），走 execCommand 注入
                tag_str = " ".join(f"#{t}" if not str(t).startswith("#") else str(t) for t in topics[:5])
                payload = f"{title}\n\n{content_text[:200]}\n\n{tag_str}"
                await ui.evaluate("""(txt) => {
                    const ed = document.querySelector("[contenteditable='true']")
                            || document.querySelector("textarea");
                    if (!ed) return false;
                    ed.focus();
                    if (ed.tagName === 'TEXTAREA') {
                        ed.value = txt;
                        ed.dispatchEvent(new Event('input', {bubbles: true}));
                    } else {
                        document.execCommand('insertText', false, txt);
                    }
                    return true;
                }""", payload)
                await asyncio.sleep(1)
                filled_text = await ui.evaluate("""() => {
                    const ed = document.querySelector("[contenteditable='true']")
                            || document.querySelector("textarea");
                    return ed ? (ed.innerText || ed.value || '') : '';
                }""")

                # 4. 原创声明：label 含「原创」的 checkbox（fail-closed）
                orig_done = False
                orig_txt = ""
                if declare_original:
                    orig_done = bool(await ui.evaluate("""() => {
                        for (const c of document.querySelectorAll("input[type='checkbox']")) {
                            let lbl = (c.closest('label') && c.closest('label').innerText) || '';
                            if (!lbl && c.id) {
                                const l = document.querySelector(`label[for='${c.id}']`);
                                if (l) lbl = l.innerText;
                            }
                            if (!lbl && c.parentElement) lbl = c.parentElement.innerText || '';
                            if (lbl.includes('原创')) { c.click(); return true; }
                        }
                        return false;
                    }"""))
                    if orig_done:
                        orig_txt = "原创标记 checkbox"
                        await asyncio.sleep(1)
                    if not orig_done and not allow_missing_flags:
                        await page.screenshot(path=str(shot_dir / "fail_original_not_found.png"),
                                              full_page=True)
                        return {"success": False, "published": False,
                                "status": "ORIGINAL_CHECKBOX_NOT_FOUND",
                                "message": "要求声明原创但 iframe 内找不到原创复选框，已中止（fail-closed）"}

                # 5. AI 声明：视频号 Web 端无此控件，如实记录
                aigc_state = "NOT_SUPPORTED_ON_WEB"
                aigc_txt = ""
                if declare_aigc:
                    page_text = await ui.evaluate("document.body.innerText")
                    hit = next((k for k in AIGC_KEYS if k in page_text), "")
                    if hit:
                        aigc_state = "CONTROL_PRESENT_BUT_UNHANDLED"
                        aigc_txt = hit
                        logger.warning(f"[CHANNELS] 页面出现「{hit}」，但未实现点击，需人工确认")
                    else:
                        logger.warning("[CHANNELS] 视频号 Web 无 AI 声明控件（需手机端补勾）")

                await page.screenshot(path=str(shot_dir / "before_publish.png"), full_page=True)

                if stop_before_publish:
                    return {"success": True, "published": False, "status": "STOPPED_BEFORE_PUBLISH",
                            "url": page.url, "declare_original": orig_done,
                            "original_label": orig_txt, "aigc_state": aigc_state,
                            "filled_text": filled_text[:200],
                            "screenshot": str(shot_dir / "before_publish.png"),
                            "message": "已上传、填文案、勾原创，停在发表前（测试档）"}

                # 6. 发表（点「发表」，若弹二次确认再点「直接发表」）
                clicked = await ui.evaluate("""() => {
                    const btn = [...document.querySelectorAll('button')]
                        .find(b => b.innerText.trim() === '发表' && !b.disabled);
                    if (!btn) return false;
                    btn.click();
                    return true;
                }""")
                if not clicked:
                    return {"success": False, "published": False, "status": "PUBLISH_BUTTON_NOT_FOUND",
                            "message": "iframe 内找不到可用的「发表」按钮"}
                await asyncio.sleep(3)
                await ui.evaluate("""() => {
                    const c = [...document.querySelectorAll('button')]
                        .find(b => b.innerText.trim() === '直接发表');
                    if (c) c.click();
                }""")

                # 7. 验证平台侧确认（成功文案 / URL 跳转）
                published = False
                evidence = ""
                for _ in range(30):
                    await asyncio.sleep(2)
                    try:
                        body = await ui.evaluate("document.body.innerText")
                    except Exception as e:  # noqa: BLE001 — 页面可能正在跳转
                        logger.debug(f"读取 iframe 正文失败（可能跳转中）: {e}")
                        body = ""
                    if any(k in body for k in ("发表成功", "已发表", "发布成功")):
                        published, evidence = True, "成功文案"
                        break
                    if "/post/create" not in page.url:
                        published, evidence = True, f"URL 跳转 {page.url}"
                        break

                await page.screenshot(path=str(shot_dir / "after_publish.png"), full_page=True)
                return {
                    "success": published,
                    "published": published,
                    "status": "PUBLISHED" if published else "PUBLISHED_UNVERIFIED",
                    "evidence": evidence,
                    "url": page.url,
                    "declare_original": orig_done,
                    "aigc_state": aigc_state,
                    "original_label": orig_txt,
                    "filled_text": filled_text[:200],
                    "screenshot": str(shot_dir / "after_publish.png"),
                    "message": ("已发表并取得平台侧证据" if published
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
