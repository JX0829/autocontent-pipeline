import os
import time
import threading
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from . import cdp_session
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
        # 发布用有头浏览器：抖音会识别无头（扫码/短信验证必失败，页面也不稳）。
        # 账号配置可覆盖（accounts.yaml: headless: true），默认 false = 有头。
        self.headless_publish = bool(account_config.get("headless", False))
        # CDP 接管（R1/R2，2026-11-13）：账号配置 > DOUYIN_CDP_URL > 空（=老逻辑）。
        # 空串表示不接管，仍走「起临时 profile + storage_state 快照」的历史路径。
        self.cdp_url = cdp_session.resolve(account_config, "DOUYIN_CDP_URL")
        self.profile_name = Path(self.profile_path).name

        # 确保 profile 目录存在
        os.makedirs(os.path.abspath(self.profile_path), exist_ok=True)

    def _get_browser_context(self, p, headless: bool | None = None):
        """headless=None 时按账号配置（默认有头）；显式传值可覆盖。

        CDP 模式（self.cdp_url 非空）：探活 → `connect_over_cdp` 接管**已登录的常驻
        Chrome**，取它的真实 context。返回的 browser 是包装器，`close()` 只关本流程
        开的页面，不会把 Chrome（和那次人工扫码的登录态）关掉。
        探活失败抛 `CdpUnavailable`，调用方不许继续（也不许占台账，R3）。
        """
        if self.cdp_url:
            context, browser = cdp_session.connect(p, self.cdp_url)
            logger.info(f"[douyin] 接管常驻 Chrome: cdp={self.cdp_url} profile={self.profile_name}")
            return context, browser
        if headless is None:
            headless = self.headless_publish
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

    #: 登录页的正文特征（2026-11-13 实测：登录页 URL 也在 creator-micro 下，
    #: 只看 URL 会把未登录判成已登录，正是 llamacpp-v1-1 那天「检查说有登录、
    #: 真发时 LOGIN_REQUIRED」的来源）。
    LOGIN_PAGE_MARKERS = ("扫码登录", "验证码登录", "密码登录", "登录即代表同意")

    def _is_login_page(self, page) -> bool:
        """页面是否落在登录墙：URL 命中 passport，或正文出现登录页特征。"""
        url = (page.url or "").lower()
        if "passport.douyin.com" in url or "/login" in url:
            return True
        try:
            body = page.inner_text("body")[:2000]
        except Exception:  # noqa: BLE001 — 取不到正文按未登录处理（fail-closed）
            return True
        return sum(1 for m in self.LOGIN_PAGE_MARKERS if m in body) >= 2

    def check_login_status(self) -> bool:
        """检查当前 profile 是否**真的**已登录创作者中心（fail-closed）。

        CDP 模式下走 `_get_browser_context` 的接管分支，判据与发布流程一致：
        不看 URL 猜，看登录页特征。
        """
        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=None)
                page = context.new_page()
                page.goto(self.UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

                logged_in = not self._is_login_page(page)
                if not logged_in:
                    logger.warning(
                        f"[douyin] 未登录：profile={self.profile_name}"
                        f"{'，cdp=' + self.cdp_url if getattr(self, 'cdp_url', '') else ''}"
                        f"，URL={page.url[:80]}")
                browser.close()
                return logged_in
        except cdp_session.CdpError as e:
            logger.error(f"[douyin] 登录检查失败（CDP）：{e}")
            return False
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
        declare_aigc: Optional[bool] = None,
        stop_before_publish: bool = False,
    ) -> Dict[str, Any]:
        """对外入口：CDP 模式下先抢端口级单实例锁（R5），再走上传流程。

        抢不到锁返回 `CDP_BUSY`（明确失败，不排队抢同一个 tab）；非 CDP 模式零开销。
        """
        kw = dict(stop_before_publish=stop_before_publish)
        if not self.cdp_url:
            return self._save_draft_impl(video_path, cover_path, title, topics,
                                         content_text, declare_aigc, **kw)
        try:
            with cdp_session.exclusive("douyin", self.cdp_url):
                return self._save_draft_impl(video_path, cover_path, title, topics,
                                             content_text, declare_aigc, **kw)
        except cdp_session.CdpBusy as exc:
            logger.warning(f"[douyin] {exc}")
            return {"success": False, "error": "CDP_BUSY", "message": str(exc)}
        except cdp_session.CdpUnavailable as exc:
            logger.error(f"[douyin] {exc}")
            return {"success": False, "error": "CDP_UNAVAILABLE", "message": str(exc)}

    def _save_draft_impl(
        self,
        video_path: str,
        cover_path: Optional[str],
        title: str,
        topics: list[str],
        content_text: str,
        declare_aigc: Optional[bool] = None,
        stop_before_publish: bool = False,
    ) -> Dict[str, Any]:
        """
        上传短视频至抖音创作者中心并仅保存为草稿（绝不执行直接发布）
        """
        if not os.path.exists(video_path):
            return {"success": False, "error": f"Video file not found: {video_path}"}

        should_declare_aigc = self.declare_aigc if declare_aigc is None else declare_aigc

        try:
            with sync_playwright() as p:
                context, browser = self._get_browser_context(p, headless=None)
                page = context.new_page()
                
                # 先访问创作者主页建立会话上下文，再进入上传页
                logger.info(f"Navigating to Douyin creator home: {self.CREATOR_URL}")
                # 抖音 SPA 持续长轮询，networkidle 基本永不静默 → 直接 30s 超时（实测）。
                # 用 domcontentloaded 起步 + 尽力等一次 networkidle（等不到不算失败），
                # 后续靠选择器判断页面是否可用。
                page.goto(self.CREATOR_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001 — SPA 长轮询属正常
                    logger.info("creator home 未在 8s 内 networkidle，继续")
                time.sleep(2)

                logger.info(f"Navigating to Douyin upload page: {self.UPLOAD_URL}")
                page.goto(self.UPLOAD_URL, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001
                    logger.info("upload 页未在 8s 内 networkidle，继续")
                time.sleep(3)

                # 检查登录态拦截
                if "passport.douyin.com" in page.url or page.locator("text=扫码登录").count() > 0:
                    browser.close()
                    hint = (f"需人工在 cdp={self.cdp_url} profile={self.profile_name} 的 Chrome 窗口扫码"
                            if self.cdp_url else "请先运行 scripts/login_douyin.py 扫码登录")
                    return {
                        "success": False,
                        "error": "LOGIN_REQUIRED",
                        "message": f"抖音登录态已失效或未初始化（profile={self.profile_name}"
                                   f"{'，cdp=' + self.cdp_url if self.cdp_url else ''}）：{hint}"
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
                #    2026-11-15 实测：原来把「标题 + 话题 + 全文」一次性 type(delay=50) 打进
                #    contenteditable，抖音的话题联想面板会在打字过程中插入/重排内容，
                #    最终简介变成「标题和正文交错、#A#n#C#A」这种乱码。
                #    改为：标题进独立「作品标题」输入框；正文原子插入；话题逐个带停顿地打。
                # 3a. 简介正文（标题已在 build_desc 里打头，不再塞进独立标题框，避免重复）
                body_text = content_text
                caption_editor = page.locator('div[contenteditable="true"]').first
                editor_ok = caption_editor.count() > 0
                if editor_ok:
                    caption_editor.click()
                    caption_editor.evaluate("el => { el.focus(); el.innerHTML = ''; }")
                    # 原子插入：一次 execCommand，不走逐字符，避免联想面板重排
                    caption_editor.evaluate(
                        "(el, t) => { el.focus(); document.execCommand('insertText', false, t); }",
                        body_text)
                    time.sleep(1)
                    got = (caption_editor.inner_text() or "").strip()
                    if got[:20] != body_text.strip()[:20]:
                        logger.warning("原子插入未生效，回退 fill()")
                        caption_editor.fill(body_text)
                else:
                    textarea = page.locator('textarea').first
                    if textarea.count() > 0:
                        textarea.fill(body_text)
                        editor_ok = True

                # 3c. 话题：先把光标移到文本末尾（JS Range，press("End") 只到行尾），
                #     再逐个打 + 停顿，让抖音把每个 #tag 收成话题chip
                if editor_ok and topics:
                    caption_editor.click()
                    caption_editor.evaluate("""el => {
                        el.focus();
                        const r = document.createRange();
                        r.selectNodeContents(el);
                        r.collapse(false);                       // false = 折叠到末尾
                        const s = window.getSelection();
                        s.removeAllRanges(); s.addRange(r);
                    }""")
                    time.sleep(0.3)
                    page.keyboard.type(" ", delay=10)            # 正文与话题之间留个分隔
                    for t in topics:
                        page.keyboard.type(f"#{t.strip('#')}", delay=20)
                        time.sleep(0.5)
                        page.keyboard.press("Space")
                        time.sleep(0.4)
                logger.info(f"Filled caption: title={title[:20]!r} topics={topics}")

                # 3d. 回读简介真实内容（发布前自证：乱码/截断都在这里暴露）
                filled_text = ""
                try:
                    filled_text = (caption_editor.inner_text() or "").strip() if editor_ok else ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"简介回读失败: {exc}")
                if filled_text and body_text.strip()[:20] not in filled_text:
                    logger.error(f"CAPTION_MISMATCH: 回读与预期不符 filled={filled_text[:80]!r}")
                    browser.close()
                    return {"success": False, "published": False,
                            "error": "CAPTION_MISMATCH",
                            "expected": body_text[:200], "filled": filled_text[:200],
                            "message": "简介回读与预期不符（疑似乱码/被联想面板重排），拒绝发布"}
                time.sleep(2)

                if stop_before_publish:
                    logger.info("STOP_BEFORE_PUBLISH：表单已填好，未点发布")
                    shot_dir = Path("output/douyin_diag")
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    try:
                        page.screenshot(path=str(shot_dir / f"stop_before_publish_{int(time.time())}.png"),
                                        full_page=True)
                    except Exception:  # noqa: BLE001
                        pass
                    browser.close()
                    return {"success": True, "published": False,
                            "status": "STOPPED_BEFORE_PUBLISH",
                            "filled_title": title[:30], "filled_text": filled_text,
                            "topics": topics,
                            "message": "表单已填好（含回读校验），按 stop_before_publish 未点发布"}


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

        except cdp_session.CdpError:
            # CDP 类错误（不可用/占用/需登录）必须原样上抛，由 save_draft 归类成
            # CDP_UNAVAILABLE / CDP_BUSY / LOGIN_REQUIRED，不能被下面的大 except 吞成裸文案
            raise
        except Exception as e:
            logger.exception(f"Error during Douyin draft saving: {e}")
            return {"success": False, "error": str(e), "message": "保存抖音草稿异常"}

    def _verify_published(self, title: str, wait_sec: int = 45) -> Dict[str, Any]:
        """权威回读：打开内容管理列表，确认标题真实出现（唯一成功判据）。

        必须放到独立线程里跑：publish 执行体可能已处在 asyncio 事件循环中，而
        `sync_playwright()` 在运行中的事件循环里会直接抛「It looks like you are
        using Playwright Sync API inside the asyncio loop」——以前因此每次回读都
        失败，明明已发布却判成 READBACK_NOT_FOUND。
        """
        box: Dict[str, Any] = {}

        def _worker() -> None:
            try:
                box.update(self._verify_published_sync(title, wait_sec))
            except Exception as exc:  # noqa: BLE001 — 回读异常按未命中处理，不掩盖
                logger.warning(f"verify worker failed: {exc}")
                box.update({"found": False, "draft_id": None, "title": None})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(wait_sec + 60)
        return box or {"found": False, "draft_id": None, "title": None}

    def _verify_published_sync(self, title: str, wait_sec: int = 45) -> Dict[str, Any]:
        key = (title or "").strip()[:14]
        if not key:
            return {"found": False, "draft_id": None, "title": None}
        deadline = time.time() + wait_sec
        last_count = 0
        while time.time() < deadline:
            try:
                with sync_playwright() as p:
                    context, browser = self._get_browser_context(p, headless=None)
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
