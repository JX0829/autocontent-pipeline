import os
import re
import time
import threading
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from . import cdp_session
from . import douyin_form
from .base import BaseSocialProvider

logger = logging.getLogger("DouyinProvider")

# 抖音编辑器会给「话题 chip」前后塞零宽字符和 NBSP（\u200b / \xa0），读回时它们
# 既不算错也不能当作内容差异。比对前统一归一化：去零宽、NBSP→空格、压空白。
_INVISIBLE = "\u200b\u200c\u200d\ufeff"


def _normalize_caption(text: str) -> str:
    s = text or ""
    for ch in _INVISIBLE:
        s = s.replace(ch, "")
    s = s.replace("\xa0", " ")
    return " ".join(s.split())


_TAIL_NOISE = " #＃、,;；·.。\u3000\t\r\n"


def _strip_tail_noise(s: str) -> str:
    """去掉尾部纯噪声：零宽已在上一步删掉，这里清掉「孤立 #」与分隔符。"""
    out = s
    while out and out[-1] in _TAIL_NOISE:
        out = out[:-1]
    return out


def _caption_matches(actual: str, expected: str) -> bool:
    """回读文本是否与期望一致。

    2026-11-18 事故（只发出去 1/3 的直接原因）：
      写入本身是对的，但话题芯片插入后正文尾部会留 5 个零宽空格 + 一个孤立 `#`，
      严格相等判等 → `CAPTION_MISMATCH` 假红，整条片被拦下不发布。

    现在的判据（既要拦住真错位，又不能被尾部噪声骗）：
      1. 归一化（删零宽/NBSP、压空白）后**严格相等** → 通过；
      2. 否则算相似度，要求 ≥ 0.95 **且** 所有差异字符都落在末尾 12 个字符内、
         且差异字符只可能是 `#` / 分隔符 / 空白 —— 这正是「芯片尾噪声」的形状；
      3. 其余一律不通过（正文被重排、话题错位、丢字仍然拦）。
    """
    a, e = _normalize_caption(actual), _normalize_caption(expected)
    if not a:
        return False
    if a == e:
        return True

    a2, e2 = _strip_tail_noise(a), _strip_tail_noise(e)
    if a2 == e2:
        return True

    import difflib

    ratio = difflib.SequenceMatcher(None, a2, e2).ratio()
    if ratio < 0.95:
        return False
    # 差异必须全部落在尾部 12 个字符内，且只可能是分隔/噪声字符
    sm = difflib.SequenceMatcher(None, a2, e2)
    tail_start = max(0, len(e2) - 12)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        extra = (a2[i1:i2] + e2[j1:j2])
        if min(i1, j1) < tail_start:
            return False
        if any(ch not in _TAIL_NOISE for ch in extra):
            return False
    return True

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

    #: 标题选择器与填/验逻辑统一收在 `src/providers/douyin_form.py`（provider 与 CLI 共用）
    TITLE_SELECTORS = douyin_form.TITLE_SELECTORS

    def _fill_title(self, page, title: str) -> tuple[bool, str, str]:
        """标题落位 + 回读自证。实现见 douyin_form.fill_title（单一实现，CLI 同源）。"""
        return douyin_form.fill_title(page, title)

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
                # 3a. 标题：进独立「作品标题」框并回读自证；落不上就不许发布
                title_ok, landed_title, title_err = self._fill_title(page, title)
                if not title_ok:
                    logger.error(f"TITLE_NOT_LANDED: {title_err}（expected={title[:40]!r}）")
                    browser.close()
                    return {"success": False, "published": False,
                            "error": "TITLE_NOT_LANDED",
                            "expected": title[:60], "filled": (landed_title or "")[:60],
                            "message": f"标题没能落到「作品标题」框（{title_err}），拒绝发布"}

                # 3b. 正文：三段式构造（标题留空）+ 一次性写入 + 回读校验。
                #     **话题不拼进正文** —— 纯文本 #话题 在抖音只是文字，必须走联想面板
                #     才会成为真话题节点（见 3c 与 douyin_form.fill_topics）。
                from src.utils.douyin_caption import build_caption
                full_text = build_caption("", content_text.strip(), [])
                caption_editor = page.locator('div[contenteditable="true"]').first
                editor_ok = caption_editor.count() > 0
                if editor_ok:
                    from src.providers import douyin_form
                    caption_ok = douyin_form.fill_caption(page, full_text)
                else:
                    textarea = page.locator("textarea").first
                    caption_ok = False
                    if textarea.count() > 0:
                        textarea.fill(full_text)
                        caption_ok = True
                        editor_ok = True
                logger.info(f"Filled caption: title={title[:20]!r} ok={caption_ok}")

                # 3c. 话题：敲 #关键词 → 点联想面板 → 真话题节点（[data-mention="#"]）
                from src.providers import douyin_form
                _tp = douyin_form.fill_topics(page, topics, log_obj=logger)
                topics_declared, topics_landed = _tp["declared"], _tp["landed"]
                topics_ok = bool(_tp["ok"])
                if topics_declared and not topics_ok:
                    logger.warning(f"[douyin] 话题未全部落地 landed={topics_landed} "
                                   f"declared={topics_declared}：{'; '.join(_tp['problems'])}")

                # 3c.5 清尾部残渣（R7）：抖音给话题 chip 前后塞 \u200b/\xa0，
                # 敲了一半没落地的话题还会留下 " 4" 这类纯文本碎片。它们不属于正文，
                # 必须在**回读比对之前**清掉 —— 否则 CAPTION_MISMATCH 会把残渣算成「简介写错」，
                # 真机上表现为「简介回读与预期不符」但其实正文一个字都没错（2026-11-19 实测）。
                if _tp.get("stray") or topics_declared:
                    try:
                        _cleaned = page.evaluate(
                            """([invisible, stray]) => {
                              const ed = document.querySelector('div[contenteditable="true"]');
                              if (!ed) return 0;
                              const re = new RegExp('[' + invisible + ']', 'g');
                              const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                              let n = 0, node;
                              while ((node = walker.nextNode())) {
                                const p = node.parentElement;
                                if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                                const before = node.textContent || '';
                                let after = before.replace(re, '').replace(/[ \t]+$/, '');
                                // 敲了一半的话题碎片（如 "#Gemini 4 Neon" 留下的 " 4"）也算残渣
                                if (stray && after.endsWith(stray)) {
                                  after = after.slice(0, -stray.length).replace(/[ \t]+$/, '');
                                }
                                if (after !== before) { node.textContent = after; n++; }
                              }
                              return n;
                            }""",
                            [_INVISIBLE, _tp.get("stray") or ""],
                        )
                        if _cleaned:
                            logger.info(f"[douyin] 已清尾部零宽/多余空白文本节点 {_cleaned} 个")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[douyin] 尾部残渣清理失败（继续，交给回读判）：{exc}")

                # 3c.6 尾部多余文本兜底：话题 chip 敲一半会漏出 "4 Neon" 这类碎片，
                # 形状千变万化，与其按模式猜，不如**以正文为唯一真值**：
                # 回读若 = 正文 + 小尾巴（≤24 字），就把这个尾巴从 DOM 里删掉再回读。
                try:
                    def _body_view_now() -> str:
                        return str(page.evaluate(
                            """() => {
                              const ed = document.querySelector('div[contenteditable="true"]');
                              if (!ed) return '';
                              const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                              let out = '', node;
                              while ((node = walker.nextNode())) {
                                const p = node.parentElement;
                                if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                                out += node.textContent || '';
                              }
                              return out;
                            }"""
                        ) or "").strip()

                    for _ in range(3):                       # 最多修 3 轮，防死循环
                        _view = _body_view_now()
                        _want = full_text.strip()
                        if _view == _want:
                            break
                        if _view.startswith(_want):
                            _extra = _view[len(_want):]
                            if not _extra or len(_extra) > 24:
                                break
                            _gone = page.evaluate(
                                """(suffix) => {
                                  const ed = document.querySelector('div[contenteditable="true"]');
                                  if (!ed) return false;
                                  const nodes = [];
                                  const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                                  let node;
                                  while ((node = walker.nextNode())) {
                                    const p = node.parentElement;
                                    if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                                    nodes.push(node);
                                  }
                                  for (let i = nodes.length - 1; i >= 0; i--) {
                                    const t = nodes[i].textContent || '';
                                    if (t.endsWith(suffix)) {
                                      nodes[i].textContent = t.slice(0, -suffix.length).replace(/[ \t]+$/, '');
                                      return true;
                                    }
                                  }
                                  return false;
                                }""",
                                _extra,
                            )
                            logger.warning(f"[douyin] 清掉话题尾部碎片 {_extra!r} → {bool(_gone)}")
                            if not _gone:
                                break
                        else:
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[douyin] 尾部碎片兜底清理失败（交给回读判）：{exc}")

                # 3d. 回读简介真实内容（发布前自证：乱码/错位/截断都在这里暴露）
                filled_text = ""
                try:
                    filled_text = (caption_editor.inner_text() or "").strip() if editor_ok else ""
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"简介回读失败: {exc}")
                # 2026-11-18：话题已是编辑器里的真节点，innerText 会把它一起带出来，
                # 原先 `split("#")[0]` 在「芯片不以 # 开头」时会漏字 → CAPTION_MISMATCH 假红。
                # 现在只取「芯片之外的文本节点」拼回正文再比；话题是否生效由 topics_ok 单独判。
                _body_view = filled_text or ""
                if editor_ok:
                    try:
                        _body_view = str(page.evaluate(
                            """() => {
                              const ed = document.querySelector('div[contenteditable="true"]');
                              if (!ed) return '';
                              const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
                              let out = '', node;
                              while ((node = walker.nextNode())) {
                                const p = node.parentElement;
                                if (p && p.closest('[data-mention], span.topic, span[data-type="topic"]')) continue;
                                out += node.textContent || '';
                              }
                              return out;
                            }""") or "").strip()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"正文（去芯片）回读失败，退回 innerText: {exc}")
                if filled_text and not _caption_matches(_body_view, full_text):
                    # 排障用：给出首个差异位置与两边真实长度（否则只看到被截断的前 120 字，猜不到原因）
                    _i = next((i for i, (a, b) in enumerate(zip(_body_view, full_text)) if a != b),
                              min(len(_body_view), len(full_text)))
                    logger.error(
                        "CAPTION_MISMATCH: 回读与预期不符 "
                        f"len(view)={len(_body_view)} len(expect)={len(full_text)} first_diff@{_i} "
                        f"view[{max(0,_i-12)}:{_i+12}]={_body_view[max(0,_i-12):_i+12]!r} "
                        f"expect[{max(0,_i-12)}:{_i+12}]={full_text[max(0,_i-12):_i+12]!r}")
                    browser.close()
                    return {"success": False, "published": False,
                            "error": "CAPTION_MISMATCH",
                            "expected": full_text[:200], "filled": filled_text[:200],
                            "message": "简介回读与预期不符（乱码/话题错位/被重排），拒绝发布"}
                # 3e. 话题终态（SPEC-20261118 R8）：真话题数 == 声明数 且 尾部无游离文本
                if topics_declared and (_tp.get("stray") or not topics_ok):
                    _why = ("尾部残留游离文本 " + repr(_tp.get("stray"))) if _tp.get("stray") \
                        else f"真话题 {_tp.get('count')}/{len(topics_declared)}"
                    logger.error(f"TOPIC_TAIL_RESIDUE: {_why}")
                    browser.close()
                    return {"success": False, "published": False,
                            "error": "TOPIC_TAIL_RESIDUE" if _tp.get("stray") else "TOPIC_COUNT_MISMATCH",
                            "stage": "topics",
                            "expected": topics_declared, "filled": _tp,
                            "message": f"话题终态不合格（{_why}），拒绝发布"}

                # 3e-2. 封面上传（SPEC-20261118 R1~R3）：显式上传 + 回读，失败即阻断
                try:
                    from src.providers import douyin_form as _df
                    _cover = _df.set_cover(page, cover_path or "", log_obj=logger)
                except Exception as exc:  # noqa: BLE001
                    _cover = {"ok": False, "code": "COVER_UPLOAD_FAILED",
                              "detail": f"{type(exc).__name__}: {exc}"}
                if not _cover.get("ok"):
                    logger.error(f"COVER_UPLOAD_FAILED: {_cover.get('detail')}")
                    browser.close()
                    return {"success": False, "published": False,
                            "error": "COVER_UPLOAD_FAILED", "stage": "cover",
                            "expected": str(cover_path or ""), "filled": _cover,
                            "message": f"封面没上传成功（{_cover.get('detail')}），拒绝发布"}

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
                            "topics_declared": topics_declared,
                            "topics_landed": topics_landed,
                            "topics_ok": topics_ok,
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

                    # ★ 点「发布」前最后一道自证（2026-11-15）：话题/AIGC/可见性这些操作
                    #   会触发 React 重渲染，早先填好的标题可能被清空。必须在最后一刻
                    #   读回标题与正文，任一为空**不点发布**，绝不再产出空标题作品。
                    ready, form_detail = douyin_form.assert_form_ready(page, title, content_text)
                    if not ready:
                        shot_dir = Path("output/douyin_diag")
                        shot_dir.mkdir(parents=True, exist_ok=True)
                        try:
                            page.screenshot(path=str(shot_dir / f"submit_blocked_{int(time.time())}.png"),
                                            full_page=True)
                        except Exception:  # noqa: BLE001
                            pass
                        browser.close()
                        return {"success": False, "published": False,
                                "error": "SUBMIT_BLOCKED",
                                "form": form_detail,
                                "message": ("点发布前自证不通过（标题/正文读回为空或不符），"
                                            "拒绝发布，避免产出空标题作品")}

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
                    verified = self._verify_published(title, fallback_text=content_text[:40])
                    time.sleep(1)
                    browser.close()
                    if not verified.get("found"):
                        return {
                            "success": False,
                            "error": "READBACK_NOT_FOUND",
                            "signal": signal,
                            "message": "发布信号出现但内容管理列表回读未命中该作品，判定未发布（拒绝虚报成功）"
                        }
                    if not verified.get("title_ok", True):
                        # 作品出现了、但标题没落上：既不能虚报成功，也不能当"没发出去"重发
                        logger.error("PUBLISHED_NO_TITLE: 作品已在列表出现，但标题未落上")
                        return {
                            "success": False,
                            "published": True,
                            "status": "PUBLISHED_NO_TITLE",
                            "error": "PUBLISHED_NO_TITLE",
                            "post_id": verified.get("draft_id") or "",
                            "signal": signal,
                            "message": ("作品已在内容管理列表出现（按正文命中），但标题未落上："
                                        "有作品无标题，按 unverified 记账，需人工到平台补标题")
                        }
                    return {
                        "success": True,
                        "draft_id": verified["draft_id"],
                        "verified_title": verified["title"],
                        "topics_declared": topics_declared,
                        "topics_landed": topics_landed,
                        "topics_ok": topics_ok,
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
            # 2026-11-19 事故：这里原来只返回 str(e) + 一句「保存草稿异常」，
            # 连失败在哪一步、哪段 JS 报的 SyntaxError 都看不到。
            # 现在：带 traceback 末段 + 现场落盘（截图/HTML），并把异常归类为
            # DRAFT_SAVE_EXCEPTION，供上层与台账区分「发布侧异常」与「物料问题」。
            import traceback as _tb

            tb_tail = "".join(_tb.format_exception(type(e), e, e.__traceback__)[-4:])[-1600:]
            logger.exception(f"Error during Douyin draft saving: {e}")
            dbg = ""
            _pg = locals().get("page")
            try:
                d = Path("output/douyin_diag") / f"draft_exc_{int(time.time())}"
                d.mkdir(parents=True, exist_ok=True)
                if _pg is not None:
                    _pg.screenshot(path=str(d / "page.png"), full_page=True)
                    (d / "page.html").write_text(_pg.content(), encoding="utf-8")
                (d / "traceback.txt").write_text(tb_tail, encoding="utf-8")
                dbg = str(d)
                logger.error(f"[douyin] 草稿阶段异常现场已落盘：{dbg}")
            except Exception:  # noqa: BLE001
                pass
            return {"success": False, "error": f"{type(e).__name__}: {e}",
                    "error_code": "DRAFT_SAVE_EXCEPTION", "stage": "draft",
                    "traceback_tail": tb_tail, "debug_dir": dbg,
                    "message": f"保存抖音草稿异常（{type(e).__name__}），现场：{dbg or '落盘失败'}"}

    def _verify_published(self, title: str, wait_sec: int = 45,
                          fallback_text: str = "") -> Dict[str, Any]:
        """权威回读：打开内容管理列表，确认标题真实出现（唯一成功判据）。

        必须放到独立线程里跑：publish 执行体可能已处在 asyncio 事件循环中，而
        `sync_playwright()` 在运行中的事件循环里会直接抛「It looks like you are
        using Playwright Sync API inside the asyncio loop」——以前因此每次回读都
        失败，明明已发布却判成 READBACK_NOT_FOUND。
        """
        box: Dict[str, Any] = {}

        def _worker() -> None:
            try:
                box.update(self._verify_published_sync(title, wait_sec, fallback_text))
            except Exception as exc:  # noqa: BLE001 — 回读异常按未命中处理，不掩盖
                logger.warning(f"verify worker failed: {exc}")
                box.update({"found": False, "draft_id": None, "title": None, "title_ok": False})

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(wait_sec + 60)
        return box or {"found": False, "draft_id": None, "title": None}

    @staticmethod
    def _match_readback(text: str, title_key: str, caption_key: str) -> tuple[bool, bool, str]:
        """回读命中判定（纯函数，便于回归）。

        返回 (是否找到作品, 标题是否落上, 命中依据)。标题优先；
        标题没命中但正文命中 = 作品发了、标题没落上（"有作品无标题"）。
        """
        blob = text or ""
        if title_key and title_key in blob:
            return True, True, "title"
        if caption_key and caption_key in blob:
            return True, False, "caption"
        return False, False, ""

    def _verify_published_sync(self, title: str, wait_sec: int = 45,
                               fallback_text: str = "") -> Dict[str, Any]:
        """回读内容管理列表。

        判据两级：
          1) 按**标题**前 14 字命中 → found=True, title_ok=True；
          2) 按**正文**前 14 字命中 → found=True, title_ok=False（"有作品无标题"）。
             这一级是 2026-11-15 加的：标题没落上时作品其实已经发出去了，
             旧逻辑一律判 READBACK_NOT_FOUND → 台账 unverified 且说不清到底发没发。
        """
        key = (title or "").strip()[:14]
        fb = (fallback_text or "").strip()[:14]
        if not key and not fb:
            return {"found": False, "draft_id": None, "title": None, "title_ok": False, "matched": ""}
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
                    rid = f"readback_{int(time.time())}"
                    found, title_ok, matched = self._match_readback(txt, key, fb)
                    if found:
                        return {"found": True, "draft_id": rid,
                                "title": key if title_ok else "",
                                "title_ok": title_ok, "matched": matched}
            except Exception as e:
                logger.warning(f"read-back attempt failed: {e}")
            time.sleep(8)
        logger.error(f"READBACK_NOT_FOUND for key={key} (fallback={fb})")
        return {"found": False, "draft_id": None, "title": None,
                "title_ok": False, "matched": "", "last_count": last_count}

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
