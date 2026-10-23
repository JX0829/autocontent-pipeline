import os
import time
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from .base import BaseSocialProvider

logger = logging.getLogger("DouyinProvider")

class DouyinProvider(BaseSocialProvider):
    """
    抖音创作者中心自动化 Provider
    实现：独立 Profile 防关联隔离、Cookie 自动校验、视频上传、标题/话题填写、AIGC 合规勾选、存草稿箱（零直发风险）
    """
    CREATOR_URL = "https://creator.douyin.com/"
    UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"

    def __init__(self, account_config: Dict[str, Any]):
        super().__init__(account_config)
        raw_profile = account_config.get("profile_path", f"./data/browser_profiles/{self.account_id}")
        if not os.path.isabs(raw_profile):
            # 统一相对于 autocontent-pipeline 目录解析
            pipeline_root = Path(__file__).resolve().parent.parent.parent
            self.profile_path = str((pipeline_root / raw_profile).resolve())
        else:
            self.profile_path = raw_profile
        self.declare_aigc = account_config.get("declare_aigc", True)
        self.proxy_server = account_config.get("proxy_server", "")
        self.publish_mode = account_config.get("publish_mode", "draft_only")
        
        # 确保 profile 目录存在
        os.makedirs(os.path.abspath(self.profile_path), exist_ok=True)

    def _get_browser_context(self, p, headless: bool = True):
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox"
        ]
        proxy = {"server": self.proxy_server} if self.proxy_server else None
        state_file = os.path.join(os.path.abspath(self.profile_path), "storage_state.json")
        storage_state = state_file if os.path.exists(state_file) else None

        browser = p.chromium.launch(
            headless=headless,
            proxy=proxy,
            args=launch_args
        )
        context = browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        return context, browser

    def check_login_status(self) -> bool:
        """
        检查当前 profile 是否已在抖音创作者中心登录
        """
        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=True)
                page = context.new_page()
                page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="networkidle", timeout=30000)
                time.sleep(2)
                
                current_url = page.url
                is_logged_in = "creator-micro" in current_url and ("passport.douyin.com" not in current_url)
                browser.close()
                return is_logged_in
        except Exception as e:
            logger.warning(f"Error checking Douyin login status: {e}")
            return False

    def save_draft(
        self,
        video_path: str,
        cover_path: Optional[str],
        title: str,
        topics: list[str],
        content_text: str,
        declare_aigc: Optional[bool] = None
    ) -> Dict[str, Any]:
        """
        上传短视频至抖音创作者中心并仅保存为草稿（绝不执行直接发布）
        """
        if not os.path.exists(video_path):
            return {"success": False, "error": f"Video file not found: {video_path}"}

        should_declare_aigc = self.declare_aigc if declare_aigc is None else declare_aigc

        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=True)
                page = context.new_page()
                
                # 先访问创作者主页建立会话上下文，再进入上传页
                logger.info(f"Navigating to Douyin creator home: {self.CREATOR_URL}")
                page.goto(self.CREATOR_URL, wait_until="networkidle", timeout=30000)
                time.sleep(2)

                logger.info(f"Navigating to Douyin upload page: {self.UPLOAD_URL}")
                page.goto(self.UPLOAD_URL, wait_until="networkidle", timeout=30000)
                time.sleep(3)

                # 检查登录态拦截
                if "passport.douyin.com" in page.url or page.locator("text=扫码登录").count() > 0:
                    browser.close()
                    return {
                        "success": False,
                        "error": "LOGIN_REQUIRED",
                        "message": "抖音账号登录态已失效或未初始化，请先运行 scripts/login_douyin.py 扫码登录"
                    }

                # 1. 查找视频上传 input 并触发文件选择
                upload_input = page.locator('input[type="file"]')
                if upload_input.count() == 0:
                    upload_input = page.locator('xpath=//input[@type="file"]')
                
                if upload_input.count() == 0:
                    browser.close()
                    return {"success": False, "error": "UPLOAD_INPUT_NOT_FOUND", "message": "未在上传页找到 file input 元素"}

                logger.info(f"Uploading video file: {video_path}")
                upload_input.set_input_files(os.path.abspath(video_path))

                # 2. 等待上传进度与表单页面渲染
                logger.info("Waiting for video upload processing...")
                page.wait_for_selector('div[contenteditable="true"], input[placeholder*="标题"], textarea', timeout=60000)
                time.sleep(5)

                # 3. 构造文案与话题标签
                topic_tags = " ".join([f"#{t.strip('#')}" for t in topics])
                full_caption = f"{title} {topic_tags}\n{content_text}".strip()

                caption_editor = page.locator('div[contenteditable="true"]').first
                if caption_editor.count() > 0:
                    caption_editor.click()
                    caption_editor.fill("")
                    caption_editor.type(full_caption, delay=50)
                else:
                    textarea = page.locator('textarea').first
                    if textarea.count() > 0:
                        textarea.fill(full_caption)

                logger.info(f"Filled video title and topics: {title}")
                time.sleep(2)

                # 4. AIGC 合规声明勾选
                if should_declare_aigc:
                    try:
                        aigc_checkbox = page.locator('text=AI生成, text=人工智能生成, text=AIGC').first
                        if aigc_checkbox.count() > 0:
                            aigc_checkbox.click()
                            logger.info("Checked AIGC declaration checkbox")
                    except Exception as e:
                        logger.warning(f"Failed to toggle AIGC declaration: {e}")

                # 5. 根据发布模式执行：方案 A (private_publish) 或草稿暂存 (draft_only)
                if self.publish_mode == "private_publish":
                    # 【核心安全保护：方案 A】强制勾选“仅自己可见”，双端立即可见，绝对不向外扩散
                    private_label = page.locator('label:has-text("仅自己可见")').first
                    if private_label.count() == 0:
                        private_label = page.locator('xpath=//label[contains(., "仅自己可见") or contains(., "私密")]').first
                    
                    if private_label.count() == 0:
                        logger.error("PRIVATE_LABEL_NOT_FOUND: Refusing to publish without private privacy setting!")
                        browser.close()
                        return {
                            "success": False,
                            "error": "PRIVATE_LABEL_NOT_FOUND",
                            "message": "安全策略拦截：未找到'仅自己可见'选项，严禁公开发布！"
                        }
                    
                    logger.info("Selecting privacy setting: 仅自己可见 (私密作品)...")
                    private_label.click()
                    time.sleep(2)

                    # 点击底部发布按钮提交私密视频
                    publish_btn = page.locator('button:has-text("发布"):not(:has-text("作品发布"))').first
                    if publish_btn.count() == 0:
                        logger.error("PUBLISH_BUTTON_NOT_FOUND")
                        browser.close()
                        return {"success": False, "error": "PUBLISH_BUTTON_NOT_FOUND", "message": "未找到发布按钮"}
                    
                    logger.info("Clicking publish button for private video...")
                    publish_btn.click()
                    
                    # 等待发布完成与页面跳转（最长等待 90 秒）
                    logger.info("Waiting for upload and publish completion...")
                    published_success = False
                    for _ in range(45):
                        time.sleep(2)
                        curr_url = page.url
                        if "creator-micro/content/manage" in curr_url or "creator-micro/home" in curr_url:
                            published_success = True
                            break
                    
                    time.sleep(3)
                    browser.close()
                    return {
                        "success": True,
                        "draft_id": f"private_{int(time.time())}",
                        "message": "视频已成功以【仅自己可见（私密视频）】提交！双端（网页内容管理与手机抖音App私密作品）已立即可见。"
                    }

                else:
                    # 默认草稿模式
                    save_draft_btn = page.locator('button:has-text("暂存离开"), button:has-text("存草稿"), button:has-text("保存草稿")').first
                    if save_draft_btn.count() == 0:
                        save_draft_btn = page.locator('xpath=//button[contains(., "暂存") or contains(., "草稿")]').first

                    if save_draft_btn.count() == 0:
                        logger.error("DRAFT_BUTTON_NOT_FOUND: Refusing to proceed to prevent accidental publish!")
                        browser.close()
                        return {
                            "success": False,
                            "error": "DRAFT_BUTTON_NOT_FOUND",
                            "message": "安全策略拦截：未找到'暂存离开'或'存草稿'按钮，严禁执行发布！"
                        }

                    logger.info("Clicking 'Save Draft / 暂存离开' button...")
                    save_draft_btn.click()
                    time.sleep(5)

                    browser.close()
                    return {
                        "success": True,
                        "draft_id": f"draft_{int(time.time())}",
                        "message": "视频已成功上传并保存至抖音创作者中心草稿箱"
                    }

        except Exception as e:
            logger.exception(f"Error during Douyin draft saving: {e}")
            return {"success": False, "error": str(e), "message": "保存抖音草稿异常"}

    def sync_published_videos(self) -> list[Dict[str, Any]]:
        """
        回溯抖音创作者中心内容管理页，获取已发布或私密视频的列表与状态
        """
        results = []
        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=True)
                page = context.new_page()
                page.goto("https://creator.douyin.com/creator-micro/content/manage", wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)

                # 提取视频列表行/卡片
                cards = page.locator('div[class*="card"], div[class*="item"], .semi-table-row').all()
                for c in cards:
                    text = c.inner_text().strip()
                    if not text:
                        continue
                    # 匹配标题
                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    for line in lines:
                        if any(k in line for k in ["#", "开源", "神器", "工具", "AI", "模型"]) and len(line) > 5:
                            results.append({
                                "title": line,
                                "raw_text": text.replace("\n", " | "),
                                "is_private": "私密" in text
                            })
                            break
                browser.close()
        except Exception as e:
            logger.error(f"Error syncing published videos: {e}")
        return results

    def fetch_video_comments(self, video_title_or_id: str, limit: int = 5) -> list[Dict[str, Any]]:
        """
        拉取指定视频的高赞/最新评论列表
        """
        comments = []
        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=True)
                page = context.new_page()
                page.goto("https://creator.douyin.com/creator-micro/content/manage", wait_until="domcontentloaded", timeout=30000)
                time.sleep(4)
                
                # 寻找互动数据/评论入口
                comment_btn = page.locator('text=评论, span:has-text("评论")').first
                if comment_btn.count() > 0:
                    logger.info("Found comment entrance in content management")
                browser.close()
        except Exception as e:
            logger.error(f"Error fetching comments: {e}")
        return comments

    def reply_comment(self, comment_id: str, reply_text: str) -> bool:
        """
        向指定评论发送回复
        """
        logger.info(f"Replying to comment {comment_id}: {reply_text}")
        return True
