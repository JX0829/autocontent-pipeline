"""
src/providers/channels_cdp.py
微信视频号 (Channels) CDP 自动化发布/草稿 Provider
基于独立浏览器会话打通视频号助手 (channels.weixin.qq.com) 稿件上传与草稿留存
"""

import os
import sys
import json
import time
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
        # 账号级「有头/无头」标记，与抖音同口径（config/accounts.yaml 的 headless）。
        # 2026-11-15 之前这里**根本没读**这个键 —— 账号里写 headless: false 也不生效，
        # 真正决定形态的是 publish_live(headless=True) 的硬编码默认值（无头）。
        self.headless_publish = bool(account_config.get("headless", False))

    def _warn_if_headless(self) -> bool:
        """常驻实例是不是无头；是的话大声报出来（用户要求视频号必须有头）。"""
        try:
            from .channels_session import running_headless
            flag = running_headless()
        except Exception as exc:  # noqa: BLE001 — 探测失败不影响主流程
            logger.debug(f"[CHANNELS] 无头探测失败: {type(exc).__name__}: {exc}")
            return False
        if flag:
            logger.error(
                "[CHANNELS] 当前常驻 Chrome 是**无头**实例（--headless）：无头会被平台风控/"
                "登录态校验打回。请先 `python -m src.providers.channels_session stop` 再重起有头。"
            )
        return bool(flag)

    def check_login_status(self) -> bool:
        """检查视频号会话**是否真的已登录**。

        2026-11-12 起不再用 `storage_state.json` 存在与否做判据（快照会与持久化
        profile 脱节，出现「检查说有登录、发布跳登录页」）。现在：
          1) 常驻 Chrome 的 CDP 9334 没起来就无头自愈拉起（cookie 在 profile 里，
             不关窗就不需要重扫）；
          2) 真打开后台首页看是否被踢到 login.html —— 只探端口不算「登录可用」。
        """
        import asyncio

        from .channels_session import ChannelsSessionError, ensure, verify_login
        try:
            # 2026-11-15：视频号必须用**有头**浏览器（用户明确要求，且无头实例会被
            # 微信风控/登录态校验打回）。ensure(headless=True) 会在 9334 没起来时拉起
            # 一个 --headless=new 的**常驻**实例，之后所有发布会复用这个无头进程。
            cdp = ensure(headless=False)
            self._warn_if_headless()
            ok, url = asyncio.run(verify_login(cdp))
        except ChannelsSessionError as exc:
            logger.warning("[CHANNELS] 会话不可用: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001 — 探活失败按未登录处理（fail-closed）
            logger.warning("[CHANNELS] 登录态校验失败: %s", exc)
            return False
        if not ok:
            logger.warning("[CHANNELS] 登录态已失效（%s），需扫码：scripts/channels_login.py", url)
        return ok

    # ────────────────────────── 真实发布（CDP / Playwright）──────────────────────────
    # 实测（2026-11-10）：直接访问 /platform/post/create 会被重定向回 /platform 首页，
    # 必须先进首页再点「发表视频」按钮才会真正进入发布页。这是视频号前端的硬约束。
    HOME_URL = "https://channels.weixin.qq.com/platform"
    POST_URL = "https://channels.weixin.qq.com/platform/post/create"

    def _state_path(self) -> Path:
        """排障备份路径（评论轮询脚本仍在用），**不再**作为发布登录态来源。"""
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
        headless: bool | None = None,
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
        首次使用请先跑 `scripts/probe_channels.py` 打印真实标签再调匹配词。
        """
        import asyncio

        from .channels_session import CDP_URL, ChannelsSessionError, ensure

        # 浏览器形态优先级：显式参数 > 账号配置(config/accounts.yaml: headless) > 默认有头。
        # 视频号**不允许**无头：无头实例会被风控/登录态校验打回，且常驻进程一旦无头，
        # 后续所有发布都复用它（2026-11-15 用户明确要求）。
        eff_headless = self.headless_publish if headless is None else headless
        if eff_headless:
            logger.warning("[CHANNELS] 显式要求无头（headless=true）：视频号会被风控/登录态打回，"
                           "仅在排障时短暂使用")

        # 登录态来源统一为「常驻 Chrome + 持久化 profile」：没起来就自愈拉起，
        # 被别的进程占用时 ensure() 会给可执行提示（关窗 / kill pid / --stop）
        try:
            cdp_url = ensure(headless=eff_headless)
        except ChannelsSessionError as exc:
            return {"success": False, "published": False, "status": "CDP_UNAVAILABLE",
                    "message": str(exc)}
        self._warn_if_headless()

        return asyncio.run(self._publish_live_async(
            video_path=video_path, cover_path=cover_path, title=title,
            content_text=content_text, topics=topics,
            declare_original=declare_original, declare_aigc=declare_aigc,
            dry_run=dry_run, allow_missing_flags=allow_missing_flags, headless=eff_headless,
            stop_before_publish=stop_before_publish, cdp_url=cdp_url))


    async def _upload_cover_async(self, ui, page, cover_path: str) -> Dict[str, Any]:
        """上传自定义封面并回读（SPEC-20261118 R9）。

        视频号发布表单在 wujie iframe 里：元素只能用 frame.evaluate 找，
        上传必须用「JS 点隐藏 input + expect_file_chooser 接管」。
        失败 → {"ok": False, "code": "COVER_UPLOAD_FAILED"}，上层阻断发布。
        """
        import asyncio
        import os
        import tempfile

        path = str(cover_path or "")
        if not path or not os.path.exists(path):
            return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": f"封面文件不存在: {path!r}"}

        opened = await ui.evaluate("""(texts) => {
            const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
            const all = [...document.querySelectorAll('div,span,button,label,a,p')];
            for (const t of texts) {
                const el = all.find(e => vis(e) && (e.innerText || '').trim() === t);
                if (el) { el.click(); return t; }
            }
            for (const t of texts) {
                const el = all.find(e => vis(e) && (e.innerText || '').includes(t)
                                          && (e.innerText || '').trim().length <= t.length + 6);
                if (el) { el.click(); return t; }
            }
            return '';
        }""", ["更换封面", "编辑封面", "上传封面", "选择封面", "设置封面"])
        if not opened:
            # 2026-11-18 真机实测（9334，编辑态 + 发布态两处都试过）：
            # 视频号 Web 端发布表单**没有封面控件** —— 可见 label 只有
            # 视频管理/视频描述/短标题/位置/合集/链接/活动/定时发表/声明原创/视频标注，
            # 全页唯一命中「封面」的是「红包封面」（无关，且 w=h=0）。
            # 因此这里不再当失败阻断（会把整条片卡死），而是如实记「Web 端不支持」，
            # 交上层决定（片头烧帧方案 / 手机端手动设置）。
            return {"ok": True, "code": "COVER_NOT_SUPPORTED_ON_WEB",
                    "detail": "视频号 Web 端无封面控件（真机实测），已跳过；"
                              "封面由平台抽帧决定，需片头帧方案或手机端手动设置"}
        logger.info(f"[CHANNELS] 已点击封面入口：{opened}")
        await asyncio.sleep(1.2)

        # 隐藏 input 坐标 0x0，直接 set_input_files 在 wujie 里不稳 → 用 file_chooser 接管
        has_input = await ui.evaluate("""() => {
            const ins = [...document.querySelectorAll('input[type=file]')]
                .filter(i => /image/.test(i.accept || '') || (i.accept || '') === '');
            if (!ins.length) return false;
            ins[ins.length - 1].click();
            return true;
        }""")
        if not has_input:
            return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": "封面弹层里没有图片上传 input"}
        try:
            async with page.expect_file_chooser(timeout=15000) as fc_info:
                pass
            chooser = await fc_info.value
            await chooser.set_files(os.path.abspath(path))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                    "detail": f"封面文件选择失败: {type(exc).__name__}: {exc}"}
        logger.info(f"[CHANNELS] 封面上传已注入：{path}")

        preview = None
        for _ in range(40):
            await asyncio.sleep(0.5)
            preview = await ui.evaluate("""() => {
                const vis = e => !!(e && e.offsetWidth && e.offsetHeight);
                const imgs = [...document.querySelectorAll('img')].filter(i => vis(i) && i.naturalWidth > 60);
                if (!imgs.length) return null;
                const i = imgs[imgs.length - 1];
                const r = i.getBoundingClientRect();
                return {x: r.x, y: r.y, w: r.width, h: r.height};
            }""")
            if preview:
                break
        if not preview:
            return {"ok": False, "code": "COVER_UPLOAD_FAILED", "detail": "上传后 20s 内没出现封面预览"}

        await ui.evaluate("""(texts) => {
            const vis = e => !!(e && (e.offsetWidth || e.offsetHeight));
            const btns = [...document.querySelectorAll('button,div[role=button],span')].filter(vis);
            for (const t of texts) {
                const b = btns.find(e => (e.innerText || '').trim() === t);
                if (b) { b.click(); return t; }
            }
            return '';
        }""", ["确定", "完成", "确认", "保存"])
        await asyncio.sleep(1.5)

        from src.providers.douyin_form import _dh_dist, _dh_window
        try:
            shot = os.path.join(tempfile.mkdtemp(), "ch_cover.png")
            await page.screenshot(path=shot, clip={"x": preview["x"], "y": preview["y"],
                                                  "width": max(8, preview["w"]),
                                                  "height": max(8, preview["h"])})
            dist = _dh_dist(_dh_window(shot), _dh_window(path))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                    "detail": f"封面回读失败（截图比对异常）: {exc}"}
        if dist > 14:
            return {"ok": False, "code": "COVER_UPLOAD_FAILED",
                    "detail": f"封面回读不一致（dHash 距离 {dist} > 14）", "dist": dist}
        logger.info(f"[CHANNELS] 封面已上传并回读通过（dHash 距离 {dist}）")
        return {"ok": True, "code": "", "detail": f"dHash 距离 {dist}", "dist": dist}

    async def _publish_live_async(self, video_path: str, cover_path: str, title: str,
                                  content_text: str, topics: list[str],
                                  declare_original: bool, declare_aigc: bool,
                                  dry_run: bool, allow_missing_flags: bool,
                                  headless: bool, stop_before_publish: bool,
                                  cdp_url: str = "") -> Dict[str, Any]:
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
            # 复用常驻 Chrome（CDP 9334）：不新建浏览器、不读 storage_state 快照，
            # 与 scripts/channels_login.py 共用同一个持久化 profile（cookie 一处在管）。
            browser = await p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context(
                viewport={"width": 1440, "height": 900})
            page = await ctx.new_page()
            try:
                await page.goto(self.HOME_URL, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)

                if "login" in page.url.lower():
                    return {"success": False, "published": False, "status": "LOGIN_EXPIRED",
                            "message": "登录态已过期，被重定向到登录页；先跑 scripts/channels_login.py 重新扫码"}

                # 进发布页：优先点「发表视频」
                # 2026-11-11 UI 改版：入口从首页挪进了内容管理列表 iframe
                # （micro/content/post/list），主文档里再也找不到该按钮。
                async def _find_create_entry():
                    try:
                        btn = await page.query_selector("button:has-text('发表视频')")
                        if btn:
                            return btn
                    except Exception as e:  # noqa: BLE001 — 页面抖动
                        logger.debug(f"[CHANNELS] 主文档找入口失败: {e}")
                    for f in page.frames:
                        if "micro/content/post/list" in (f.url or ""):
                            try:
                                hit = await f.query_selector("button:has-text('发表视频')")
                                if hit:
                                    return hit
                            except Exception as e:  # noqa: BLE001 — iframe 可能重建
                                logger.debug(f"[CHANNELS] 列表 iframe 找入口失败: {e}")
                    return None

                if "/post/create" not in page.url:
                    entry = await _find_create_entry()
                    if not entry:
                        # 退化路径：先进内容管理列表页，再找入口
                        try:
                            await page.goto(f"{self.HOME_URL.rstrip('/')}/post/list",
                                            wait_until="domcontentloaded", timeout=45000)
                        except Exception as e:  # noqa: BLE001 — 跳转失败不影响后续重试
                            logger.debug(f"[CHANNELS] 进内容管理列表页失败: {e}")
                        for _ in range(15):
                            await asyncio.sleep(1)
                            entry = await _find_create_entry()
                            if entry:
                                break
                    if not entry:
                        return {"success": False, "published": False, "status": "CREATE_ENTRY_NOT_FOUND",
                                "message": "首页与内容管理 iframe 都找不到「发表视频」入口，无法进入发布页"}
                    await entry.click()
                    for _ in range(15):
                        await asyncio.sleep(1)
                        if "/post/create" in page.url or any(
                                "micro/content/post/create" in (f.url or "") for f in page.frames):
                            break
                    if "/post/create" not in page.url and not any(
                            "micro/content/post/create" in (f.url or "") for f in page.frames):
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

                # 3. 填文案（描述编辑器 + 短标题 + 话题）
                # ⚠️ 视频号的描述编辑器是 `<div contenteditable="" data-placeholder="添加描述" class="input-editor">`
                #    contenteditable 是**空字符串**，`[contenteditable='true']` 选不到它，
                #    旧实现于是命中了「商品链接」textarea，文案写进了垃圾桶（2026-11-10 实测）。
                # 话题**不能**当纯文本粘贴：2026-11-15 实测，视频号描述框只有在我们
                # 逐字符敲入 `#关键词` 时，才会把 `#关键词` 包成
                # `<span class="hl topic" data-type="topic">`（真话题，落地到 desc.topic）。
                # 用 execCommand('insertText') 一次粘贴 → 纯文本，post_list 回读 topic=false。
                # 所以：正文照旧粘贴（保排版），话题改成敲入。
                _topic_list = [str(t).strip().lstrip("#") for t in (topics or []) if str(t).strip()][:5]
                tag_str = " ".join(f"#{t}" for t in _topic_list)
                # 按整行截断，绝不把句子/话题砍成半截
                def _cap_lines(text: str, limit: int = 360) -> str:
                    out, total = [], 0
                    for line in str(text or "").split("\n"):
                        if out and total + len(line) + 1 > limit:
                            break
                        out.append(line)
                        total += len(line) + 1
                    return "\n".join(out).strip()

                payload = f"{title}\n\n{_cap_lines(content_text)}"
                fill = await ui.evaluate("""(txt) => {
                    const ed = document.querySelector('.post-desc-box .input-editor')
                        || document.querySelector('.input-editor[data-placeholder]')
                        || document.querySelector("[contenteditable]:not([contenteditable='false'])");
                    if (!ed) return {ok: false, reason: 'editor_not_found'};
                    ed.focus();
                    ed.innerHTML = '';
                    document.execCommand('insertText', false, txt);
                    if (!(ed.innerText || '').trim()) {
                        ed.innerHTML = txt.split('\\n')
                            .map(l => `<div>${l || '<br>'}</div>`).join('');
                    }
                    ed.dispatchEvent(new InputEvent('input', {bubbles: true}));
                    return {ok: true, text: (ed.innerText || '').trim(),
                            cls: (ed.className || '').toString()};
                }""", payload)
                filled_text = str((fill or {}).get("text") or "")
                editor_cls = str((fill or {}).get("cls") or "")

                # 3b. 话题：把光标移到末尾，逐个「#关键词 + 空格」敲进去，让平台生成真话题
                topics_landed: list[str] = []
                topics_declared = list(_topic_list)
                if _topic_list:
                    try:
                        await ui.evaluate("""() => {
                            const ed = document.querySelector('.post-desc-box .input-editor')
                                || document.querySelector('.input-editor[data-placeholder]');
                            if (!ed) return false;
                            ed.focus();
                            const r = document.createRange();
                            r.selectNodeContents(ed);
                            r.collapse(false);
                            const sel = window.getSelection();
                            sel.removeAllRanges();
                            sel.addRange(r);
                            return true;
                        }""")
                        await asyncio.sleep(0.4)
                        for _tag in _topic_list:
                            await page.keyboard.type(f"#{_tag} ", delay=110)
                            await asyncio.sleep(0.6)
                        landed = await ui.evaluate("""() => {
                            const ed = document.querySelector('.post-desc-box .input-editor')
                                || document.querySelector('.input-editor[data-placeholder]');
                            if (!ed) return [];
                            return [...ed.querySelectorAll('span.topic, span[data-type=\"topic\"]')]
                                .map(e => (e.innerText || '').trim().replace(/^#/, ''));
                        }""")
                        topics_landed = [t for t in (landed or []) if t]
                        logger.info(f"[CHANNELS] 话题落地 {len(topics_landed)}/{len(_topic_list)}: {topics_landed}")
                        if len(topics_landed) < len(_topic_list):
                            # 重试一次：清掉编辑器末尾重敲（正文已就位，不动前面）
                            await page.keyboard.type(" ", delay=60)
                            for _tag in [t for t in _topic_list if t not in topics_landed]:
                                await page.keyboard.type(f"#{_tag} ", delay=130)
                                await asyncio.sleep(0.7)
                            landed2 = await ui.evaluate("""() => {
                                const ed = document.querySelector('.post-desc-box .input-editor')
                                    || document.querySelector('.input-editor[data-placeholder]');
                                if (!ed) return [];
                                return [...ed.querySelectorAll('span.topic, span[data-type=\"topic\"]')]
                                    .map(e => (e.innerText || '').trim().replace(/^#/, ''));
                            }""")
                            topics_landed = [t for t in (landed2 or []) if t]
                            logger.warning(f"[CHANNELS] 话题重试后 {len(topics_landed)}/{len(_topic_list)}: {topics_landed}")
                    except Exception as exc:  # noqa: BLE001 — 话题失败不阻断发布，但必须如实记录
                        logger.warning(f"[CHANNELS] 话题敲入失败（不阻断发布）: {type(exc).__name__}: {exc}")

                topics_ok = bool(_topic_list) and len(set(topics_landed)) >= len(set(topics_declared))
                if _topic_list and not topics_ok:
                    logger.warning(f"[CHANNELS] 话题未全部生效 declared={topics_declared} landed={topics_landed}")

                # 短标题（可选，展示在公众号/发现页）
                short_title_filled = ""
                try:
                    short_title_filled = await ui.evaluate("""(t) => {
                        const inp = document.querySelector("input[placeholder*='短标题']");
                        if (!inp) return '';
                        const setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(inp, t);
                        inp.dispatchEvent(new Event('input', {bubbles: true}));
                        return inp.value || '';
                    }""", title[:16])
                except Exception as e:  # noqa: BLE001 — 短标题是加分项，失败不阻断
                    logger.debug(f"[CHANNELS] 短标题填写失败（不阻断）: {e}")

                # fail-closed：描述为空绝不发布（否则会发出「无标题无文案」的空作品）
                if len(filled_text) < 10:
                    await page.screenshot(path=str(shot_dir / "fail_description_empty.png"),
                                          full_page=True)
                    return {"success": False, "published": False,
                            "status": "DESCRIPTION_EMPTY",
                            "editor_class": editor_cls,
                            "filled_text": filled_text[:120],
                            "screenshot": str(shot_dir / "fail_description_empty.png"),
                            "message": "描述编辑器回读为空（或过短），已中止发布（fail-closed）"}

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

                # 5b. 封面上传（SPEC-20261118 R9）：显式上传 + 回读，失败即阻断
                cover_info: Dict[str, Any] = {}
                try:
                    cover_info = await self._upload_cover_async(ui, page, cover_path)
                except Exception as exc:  # noqa: BLE001
                    cover_info = {"ok": False, "code": "COVER_UPLOAD_FAILED",
                                  "detail": f"{type(exc).__name__}: {exc}"}
                if cover_info.get("code") == "COVER_NOT_SUPPORTED_ON_WEB":
                    logger.warning(f"[CHANNELS] {cover_info.get('detail')}")
                elif not cover_info.get("ok"):
                    logger.error(f"[CHANNELS] COVER_UPLOAD_FAILED: {cover_info.get('detail')}")
                    await page.screenshot(path=str(shot_dir / "cover_failed.png"), full_page=True)
                    return {"success": False, "published": False,
                            "error": "COVER_UPLOAD_FAILED",
                            "expected": str(cover_path or ""), "filled": cover_info,
                            "screenshot": str(shot_dir / "cover_failed.png"),
                            "message": f"视频号封面没上传成功（{cover_info.get('detail')}），拒绝发布"}

                await page.screenshot(path=str(shot_dir / "before_publish.png"), full_page=True)

                if stop_before_publish:
                    return {"success": True, "published": False, "status": "STOPPED_BEFORE_PUBLISH",
                            "url": page.url, "declare_original": orig_done,
                            "original_label": orig_txt, "aigc_state": aigc_state,
                            "filled_text": filled_text[:200],
                            "topics_declared": topics_declared,
                            "topics_landed": topics_landed,
                            "topics_ok": topics_ok,
                            "cover": cover_info,
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

                # 8. 权威回读：post_list 里必须能看到这条作品的 desc.description 命中标题
                # 2026-11-20：以前只有一句「未取得平台侧证据」，日志里什么都看不到。
                # 现在每次尝试都打日志（条数/首条摘要/命中项），失败把候选摘要带出来；
                # 并且**重试 3 次**（新作品进列表有延迟），匹配口径放宽到「去标点前缀」。
                readback = {"verified": False, "reason": "未执行"}
                # 2026-11-20 P0：**权威判据只有 post_list**。
                # 以前的顺序是「DOM 成功文案 → 才算 published → 才回读」，于是
                # 只要成功文案没抓到（SPA 文案变化/弹窗未消），就既不算发布也不回读，
                # 最后只留一句「未取得平台侧证据」，日志里什么都看不到。
                # 现在：DOM 只作补充，回读**无条件执行**并轮询到 90s；
                #   post_list 命中 → published=True + post_id
                #   90s 未命中 → published=False，failed 而不是 unverified
                if True:
                    from src.providers.channels_edit import ChannelsListError, fetch_post_list

                    def _norm(s: str) -> str:
                        return "".join(ch for ch in str(s or "") if ch.isalnum())

                    want_keys = [k for k in (_norm(title)[:8], _norm(title)[:6]) if len(k) >= 4]
                    for attempt, wait_s in enumerate((0, 5, 10, 15, 20, 25, 25), start=1):
                        try:
                            if wait_s:
                                await asyncio.sleep(wait_s)
                            data = await fetch_post_list(page, 10, 1)
                            items = ((data.get("data") or {}).get("list") or [])
                            heads = [str((it.get("desc") or {}).get("description") or "")[:24]
                                     for it in items[:3]]
                            hit = None
                            for it in items:
                                d = it.get("desc") or {}
                                blob_n = _norm(d.get("description")) + _norm(
                                    json.dumps(d.get("title"), ensure_ascii=False))
                                if any(k and k in blob_n for k in want_keys):
                                    hit = {"createTime": it.get("createTime"),
                                           "description_len": len(str(d.get("description") or "")),
                                           "topic": bool(d.get("topic")),
                                           "objectId": str(it.get("objectId") or "")[:64]}
                                    break
                            logger.info(f"[CHANNELS] 回读第 {attempt} 次: 条数={len(items)} "
                                        f"首条={heads[0][:24] if heads else '∅'!r} "
                                        f"命中={'是' if hit else '否'}")
                            if hit:
                                readback = {"verified": True, "attempt": attempt, **hit}
                                published = True
                                evidence = f"post_list 命中（第 {attempt} 次回读）"
                                if hit["description_len"] < 10:
                                    published = False
                                    evidence = "回读文案为空"
                                break
                            readback = {"verified": False, "attempt": attempt,
                                        "items": len(items), "heads": heads,
                                        "reason": f"post_list 前 {len(items)} 条未见标题（关键字 {want_keys}）"}
                        except ChannelsListError as e:
                            readback = {"verified": False, "evidence_unavailable": True,
                                        "attempt": attempt,
                                        "reason": f"CHANNELS_LIST_API_FAILED: {str(e)[:120]}"}
                            logger.error(f"CHANNELS_LIST_API_FAILED（第 {attempt} 次）: {str(e)[:160]}")
                        except Exception as e:  # noqa: BLE001 — 不掩盖已取得的成功证据
                            readback = {"verified": False, "evidence_unavailable": True,
                                        "attempt": attempt, "reason": f"回读异常: {str(e)[:80]}"}
                            logger.warning(f"[CHANNELS] 回读异常（第 {attempt} 次）: {type(e).__name__}: {e}")
                    if not readback.get("verified"):
                        # 回读拿不到/没命中 → 这次就是没发出去（不是「不确定」）
                        published = False
                        evidence = "post_list 未命中（90s 内）"
                        logger.warning(f"[CHANNELS] 回读未通过: {json.dumps(readback, ensure_ascii=False)[:300]}")
                        # 现场：把还可能挂着的弹窗/红字校验一起抓下来
                        try:
                            tips = await ui.evaluate(
                                """() => [...document.querySelectorAll('.weui-desktop-dialog,.weui-desktop-toast,[class*=err]')]
                                     .filter(e => e.offsetWidth)
                                     .map(e => (e.innerText || '').trim().slice(0, 80))
                                     .filter(Boolean).slice(0, 5)""")
                            if tips:
                                logger.warning(f"[CHANNELS] 页面上仍有提示: {tips}")
                                readback["blocking_tips"] = tips
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            html = await ui.evaluate("document.body.innerHTML")
                            (shot_dir / f"publish_blocked_{int(time.time())}.html").write_text(html[:400000])
                        except Exception:  # noqa: BLE001
                            pass

                return {
                    "success": published,
                    "published": published,
                    # 2026-11-20：三态分明 —— 命中= PUBLISHED；接口挂了= UNVERIFIED（证据缺失）；
                    # 接口正常但列表里没有 = FAILED_NOT_PUBLISHED（真没发出去）
                    "status": ("PUBLISHED" if published else
                               ("PUBLISHED_UNVERIFIED" if readback.get("evidence_unavailable")
                                else "FAILED_NOT_PUBLISHED")),
                    "readback": readback,
                    # 视频号的外部 id 是 post_list 回读到的 objectId（`export/…`）。
                    # 2026-11-15 之前没往上报，executors 拿不到 id → 台账只能记 unverified，
                    # 明明发出去了却没有凭证。这里显式给出，msg_id/post_id 都填。
                    "msg_id": str(readback.get("objectId") or ""),
                    "post_id": str(readback.get("objectId") or ""),
                    "evidence": evidence,
                    "url": page.url,
                    "declare_original": orig_done,
                    "aigc_state": aigc_state,
                    "original_label": orig_txt,
                    "filled_text": filled_text[:200],
                    "editor_class": editor_cls,
                    "short_title": short_title_filled,
                    # 话题证据：declared=准备写的，landed=编辑器里真成了话题的
                    "topics_declared": topics_declared,
                    "topics_landed": topics_landed,
                    "topics_ok": topics_ok,
                    "readback": readback,
                    "screenshot": str(shot_dir / "after_publish.png"),
                    "message": ("已发表并取得平台侧证据（含 post_list 回读）" if published
                                else "已点击发表但未取得平台侧证据，请人工确认（不虚报成功）"),
                }
            finally:
                # 只关本页，**不关浏览器**：9334 上的常驻 Chrome 要留给下一次发布复用
                try:
                    await page.close()
                except Exception:  # noqa: BLE001 — 页面已被手动关闭/跳转中
                    pass

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
