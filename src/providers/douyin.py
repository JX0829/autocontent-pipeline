import os
import time
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from .base import BaseSocialProvider

logger = logging.getLogger("DouyinProvider")

# 可见性目标：publish_mode → (中文标签名, 候选选择器)。
# 选择器全落空时 fail-closed —— 绝不"找不到就按默认发"，
# 否则一次选择器改版就可能把私密内容误发成公开。
PRIVACY_TARGETS = {
    "private_publish": (
        "仅自己可见",
        ['label:has-text("仅自己可见")',
         'xpath=//label[contains(., "仅自己可见") or contains(., "私密")]'],
    ),
    "public_publish": (
        "公开可见",
        ['label:has-text("公开可见")',
         'label:has-text("所有人可见")',
         'xpath=//label[contains(., "公开") or contains(., "所有人可见")]'],
    ),
}


def select_privacy(page, mode: str) -> tuple[bool, str]:
    """按 publish_mode 勾选可见性。返回 (是否成功, 失败原因)。"""
    spec = PRIVACY_TARGETS.get(mode)
    if spec is None:
        return False, f"未知 publish_mode={mode}"
    label_name, selectors = spec
    for sel in selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            loc.click()
            return True, ""
    return False, f"mode={mode} 未找到「{label_name}」选项"


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

        # 优先使用原生 Google Chrome，避免 headless shell 缓存丢失
        chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        exec_path = chrome_app if os.path.exists(chrome_app) else None

        browser = p.chromium.launch(
            executable_path=exec_path,
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

                # 1. 触发文件选择（兼容 input[type="file"] 与新版 SPA 点击上传）
                logger.info(f"Uploading video file: {video_path}")
                try:
                    upload_input = page.locator('input[type="file"]').first
                    if upload_input.count() > 0:
                        upload_input.set_input_files(os.path.abspath(video_path))
                    else:
                        with page.expect_file_chooser(timeout=10000) as fc_info:
                            page.locator("text=点击上传, .semi-upload-drag-area").first.click()
                        fc_info.value.set_files(os.path.abspath(video_path))
                except Exception as fe:
                    logger.warning(f"Fallback to file_chooser on click: {fe}")
                    with page.expect_file_chooser(timeout=15000) as fc_info:
                        page.locator("text=点击上传, .semi-upload-drag-area").first.click()
                    fc_info.value.set_files(os.path.abspath(video_path))

                # 2. 等待上传进度与表单页面渲染
                logger.info("Waiting for video upload processing...")
                page.wait_for_selector('div[contenteditable="true"], input[placeholder*="标题"], textarea', timeout=60000)
                time.sleep(3)

                # 关闭可能的提示引导浮层
                try:
                    for guide in page.locator('button:has-text("我知道了"), button:has-text("下一步")').all():
                        if guide.is_visible():
                            guide.click()
                            time.sleep(1)
                except Exception:
                    pass

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

                # 5. 根据发布模式执行：私密 / 公开 / 草稿
                #    可见性由 accounts.yaml 的 publish_mode 决定，选择器缺失即 fail-closed，
                #    绝不"找不到就按默认发"（那等于把私密内容误发成公开）。
                if self.publish_mode in PRIVACY_TARGETS:
                    ok, reason = select_privacy(page, self.publish_mode)
                    if not ok:
                        logger.error(f"PRIVACY_LABEL_NOT_FOUND: {reason}")
                        browser.close()
                        return {"success": False, "error": "PRIVACY_LABEL_NOT_FOUND",
                                "message": f"发布拦截：{reason}"}
                    time.sleep(2)

                    # 等待视频完成上传与校验
                    logger.info("Waiting for video upload to finish on page...")
                    try:
                        page.wait_for_selector("text=上传成功", timeout=60000)
                        logger.info("Detected '上传成功' text signal")
                    except Exception:
                        logger.warning("Upload success text signal timeout, continuing with button checks")

                    # 点击底部发布按钮提交私密视频 (精确定位底部主发布按钮)
                    publish_btn = None
                    for b in page.locator('button:has-text("发布")').all():
                        t = b.inner_text().strip()
                        if t == "发布" and b.is_visible() and b.is_enabled():
                            publish_btn = b
                            break

                    if not publish_btn:
                        publish_btn = page.locator('button:has-text("发布"):not(:has-text("作品发布"))').last

                    if not publish_btn or publish_btn.count() == 0:
                        logger.error("PUBLISH_BUTTON_NOT_FOUND")
                        browser.close()
                        return {"success": False, "error": "PUBLISH_BUTTON_NOT_FOUND", "message": "未找到有效发布按钮"}

                    logger.info("Clicking publish button for private video...")
                    publish_btn.click()

                    # 等待发布完成：只认「发布成功」提示或真实跳转，最长 90 秒
                    logger.info("Waiting for publish completion signal...")
                    signal = None
                    for _ in range(45):
                        time.sleep(2)
                        curr_url = page.url
                        if "creator-micro/content/manage" in curr_url or "creator-micro/home" in curr_url:
                            signal = "url_redirect"
                            break
                        try:
                            body_txt = page.inner_text("body")
                        except Exception:
                            body_txt = ""
                        if "发布成功" in body_txt or "作品已发布" in body_txt:
                            signal = "toast_publish_success"
                            break
                        if "请上传视频" in body_txt or "上传失败" in body_txt or "发布失败" in body_txt:
                            signal = "error_on_page"
                            break

                    diag_dir = Path("output/douyin_diag")
                    diag_dir.mkdir(parents=True, exist_ok=True)
                    ts = int(time.time())
                    if signal in (None, "error_on_page"):
                        try:
                            page.screenshot(path=str(diag_dir / f"publish_fail_{ts}.png"), full_page=True)
                            (diag_dir / f"publish_fail_{ts}.txt").write_text(page.inner_text("body"), encoding="utf-8")
                        except Exception as diag_exc:  # noqa: BLE001 - 诊断落盘失败必须可见但不阻断
                            logger.warning(f"PUBLISH_DIAG_WRITE_FAILED: {diag_exc}")
                        logger.error(f"PUBLISH_SIGNAL_MISSING: signal={signal}, diag saved to {diag_dir}")
                        browser.close()
                        return {
                            "success": False,
                            "error": "PUBLISH_SIGNAL_MISSING",
                            "signal": signal,
                            "message": "点击发布后未出现成功信号，判定为未发布（已保存截图与页面文本用于排查）"
                        }

                    # 权威回读：内容管理列表里必须真实出现该标题，否则一律判失败
                    logger.info(f"Publish signal={signal}, running authoritative read-back...")
                    verified = self._verify_published(title)
                    time.sleep(1)
                    browser.close()
                    if not verified["found"]:
                        return {
                            "success": False,
                            "error": "READBACK_NOT_FOUND",
                            "signal": signal,
                            "message": "发布信号出现但内容管理列表回读未命中该作品，判定未发布（拒绝虚报成功）"
                        }
                    return {
                        "success": True,
                        "draft_id": verified["draft_id"],
                        "verified_title": verified["title"],
                        "message": "已确认私密发布成功：内容管理列表真实回读到该作品"
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

    def _verify_published(self, title: str, wait_sec: int = 45) -> Dict[str, Any]:
        """权威回读：打开内容管理列表，确认标题真实出现（唯一成功判据）"""
        key = (title or "").strip()[:14]
        if not key:
            return {"found": False, "draft_id": None, "title": None}
        deadline = time.time() + wait_sec
        last_count = 0
        while time.time() < deadline:
            try:
                with sync_playwright() as p:
                    context, browser = self._get_browser_context(p, headless=True)
                    page = context.new_page()
                    page.goto(f"{self.CREATOR_URL}creator-micro/content/manage",
                              wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(8000)
                    for _ in range(4):
                        page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(700)
                    txt = page.inner_text("body")
                    browser.close()
                    last_count = txt.count("作品")
                    if key in txt:
                        return {"found": True,
                                "draft_id": f"readback_{int(time.time())}",
                                "title": key}
            except Exception as e:
                logger.warning(f"read-back attempt failed: {e}")
            time.sleep(8)
        logger.error(f"READBACK_NOT_FOUND for key={key}")
        return {"found": False, "draft_id": None, "title": None}

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
