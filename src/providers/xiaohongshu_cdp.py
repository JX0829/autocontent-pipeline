import os
import json
import time
import logging
import urllib.parse
from typing import Optional, List, Dict, Any
from playwright.sync_api import sync_playwright

logger = logging.getLogger("XiaohongshuCDP")

class XiaohongshuCDPProvider:
    """
    通过常驻真实 Brave 浏览器 (CDP 9222 端口) 接入小红书。
    零自动化指纹 WAF 风险，持久免登，用于评论拉取、智能回复与冷启动种草。
    """
    def __init__(self, cdp_url: str = "http://127.0.0.1:9222", profile_id: str = "637067b5000000001f015e4a"):
        self.cdp_url = cdp_url
        self.profile_id = profile_id

    def check_login_status(self) -> bool:
        """检查 Brave 浏览器中是否已登录小红书"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                cookies = context.cookies()
                has_session = any(c['name'] == 'web_session' and len(c['value']) > 10 for c in cookies)
                browser.close()
                return has_session
        except Exception as e:
            logger.error(f"CDP check_login_status error: {e}")
            return False

    def get_recent_notes(self, limit: int = 5) -> List[Dict[str, Any]]:
        """从个人主页直接读取近期笔记卡片信息"""
        notes = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                
                profile_url = f"https://www.xiaohongshu.com/user/profile/{self.profile_id}"
                page.goto(profile_url, wait_until="networkidle")
                page.wait_for_timeout(2000)

                note_items = page.query_selector_all("section.note-item, .note-item")
                for el in note_items[:limit]:
                    title_el = el.query_selector(".title, .footer .title")
                    title = title_el.inner_text().strip() if title_el else ""
                    link_el = el.query_selector("a[href*='/explore/'], a")
                    href = link_el.get_attribute("href") if link_el else ""
                    feed_id = ""
                    if "/explore/" in href:
                        feed_id = href.split("/explore/")[1].split("?")[0]
                    notes.append({
                        "title": title,
                        "href": href,
                        "feed_id": feed_id
                    })
                browser.close()
        except Exception as e:
            logger.error(f"CDP get_recent_notes error: {e}")
        return notes

    def get_mentions_and_comments(self, limit: int = 20) -> List[Dict[str, Any]]:
        """通过真实 Brave 浏览器抓取通知中心（评论和@）"""
        notifications = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()

                raw_items = []
                def handle_response(response):
                    if "mentions" in response.url:
                        try:
                            data = response.json()
                            if data.get("success"):
                                raw_items.extend(data.get("data", {}).get("message_list", []))
                        except Exception:
                            pass

                page.on("response", handle_response)
                # 无论当前是否在 notification，先切 explore 确保强制重新触发 mentions 请求
                page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded")
                page.wait_for_timeout(1000)
                page.goto("https://www.xiaohongshu.com/notification", wait_until="domcontentloaded")
                page.wait_for_timeout(3500)

                for m in raw_items[:limit]:
                    comment_info = m.get("comment_info", {})
                    user_info = m.get("user_info", {})
                    item_info = m.get("item_info", {})

                    comment_id = comment_info.get("id")
                    comment_text = (comment_info.get("content") or "").strip()
                    user_name = (user_info.get("nickname") or "未知用户").strip()
                    feed_id = item_info.get("id", "")
                    feed_title = (item_info.get("content") or "").strip()

                    if comment_id and comment_text:
                        notifications.append({
                            "comment_id": comment_id,
                            "comment_text": comment_text,
                            "user_name": user_name,
                            "feed_id": feed_id,
                            "feed_title": feed_title,
                            "from": {"nickname": user_name},
                            "raw": m
                        })
                browser.close()
        except Exception as e:
            logger.error(f"CDP get_mentions_and_comments error: {e}")
        return notifications

    def reply_comment(self, comment_id: str, content: str, user_name: str = "", comment_text: str = "", max_retries: int = 3) -> bool:
        """
        通过真实 Brave 浏览器在通知页面进行原位智能回复。
        支持失败重试机制（结合指数退避，确保高可用）。
        """
        for attempt in range(1, max_retries + 1):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else context.new_page()

                    if "xiaohongshu.com/notification" not in page.url:
                        page.goto("https://www.xiaohongshu.com/notification", wait_until="networkidle")
                        page.wait_for_timeout(2000)

                    page.wait_for_selector(".tabs-content-container > .container", timeout=10000)
                    rows = page.query_selector_all(".tabs-content-container > .container")

                    target_row = None
                    for r in rows:
                        u = r.query_selector(".user-info a")
                        c = r.query_selector(".interaction-content")
                        u_name = u.inner_text().strip() if u else ""
                        c_text = c.inner_text().strip() if c else ""

                        if user_name and user_name in u_name:
                            target_row = r
                            break
                        if comment_text and (comment_text[:10] in c_text or c_text[:10] in comment_text):
                            target_row = r
                            break

                    if not target_row:
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Row not found for {user_name}: {comment_text[:20]}")
                        browser.close()
                        time.sleep(2 ** attempt)
                        continue

                    reply_btn = target_row.query_selector(".action-reply")
                    if not reply_btn:
                        logger.warning(f"[Attempt {attempt}/{max_retries}] .action-reply not found for {user_name}")
                        browser.close()
                        time.sleep(2 ** attempt)
                        continue

                    reply_btn.click()
                    page.wait_for_timeout(800)

                    textarea = target_row.query_selector(".comment-input")
                    if not textarea:
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Textarea not found for {user_name}")
                        browser.close()
                        time.sleep(2 ** attempt)
                        continue

                    textarea.click()
                    textarea.fill("")
                    page.keyboard.type(content, delay=35)
                    page.wait_for_timeout(600)

                    submit_btn = target_row.query_selector(".submit")
                    if not submit_btn:
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Submit button not found for {user_name}")
                        browser.close()
                        time.sleep(2 ** attempt)
                        continue

                    post_response_ok = {"val": False}
                    def on_post_response(res):
                        if "comment" in res.url and res.request.method == "POST":
                            try:
                                data = res.json()
                                if data.get("success") or data.get("code") == 0:
                                    post_response_ok["val"] = True
                            except Exception:
                                pass

                    page.on("response", on_post_response)
                    submit_btn.click()
                    page.wait_for_timeout(3000)

                    is_closed = target_row.query_selector(".comment-input") is None
                    if post_response_ok["val"] or is_closed:
                        logger.info(f"✅ CDP reply_comment successfully sent to {user_name} ({comment_id}): {content}")
                        browser.close()
                        return True
                    else:
                        logger.warning(f"[Attempt {attempt}/{max_retries}] Reply submission check pending/unconfirmed for {user_name}")
                        browser.close()
            except Exception as e:
                logger.error(f"[Attempt {attempt}/{max_retries}] CDP reply_comment error: {e}")

            if attempt < max_retries:
                time.sleep(2 ** attempt)

        return False

    def get_note_detail_and_comments(self, feed_id: str) -> Dict[str, Any]:
        """通过主页点击卡片弹窗方式安全获取指定笔记详情与评论"""
        result = {"success": False, "title": "", "comments": [], "no_comment": True}
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()

                profile_url = f"https://www.xiaohongshu.com/user/profile/{self.profile_id}"
                page.goto(profile_url, wait_until="networkidle")
                page.wait_for_timeout(2000)

                # 寻找对应的笔记卡片并点击
                note_items = page.query_selector_all("section.note-item, .note-item")
                target_card = None
                for el in note_items:
                    link_el = el.query_selector(f"a[href*='{feed_id}']")
                    if link_el:
                        target_card = el
                        break

                if not target_card and note_items:
                    target_card = note_items[0]

                if target_card:
                    target_card.click()
                    page.wait_for_timeout(2500)

                    data = page.evaluate('''() => {
                        const title = document.querySelector('.title, #detail-title')?.innerText?.trim() || '';
                        const desc = document.querySelector('.desc, #detail-desc')?.innerText?.trim() || '';
                        const comments = Array.from(document.querySelectorAll('.comment-item, .parent-comment')).map(c => {
                            const author = c.querySelector('.author, .user-name')?.innerText?.trim() || '';
                            const content = c.querySelector('.content, .comment-text')?.innerText?.trim() || '';
                            const id = c.getAttribute('id') || c.getAttribute('data-id') || '';
                            return { id, author, content };
                        });
                        return { title, desc, comments };
                    }''')
                    result["success"] = True
                    result["title"] = data.get("title", "")
                    result["desc"] = data.get("desc", "")
                    result["comments"] = data.get("comments", [])
                    result["no_comment"] = len(result["comments"]) == 0

                    page.keyboard.press("Escape")
                    page.wait_for_timeout(1000)

                browser.close()
        except Exception as e:
            logger.error(f"CDP get_note_detail_and_comments error: {e}")
        return result

    def post_seed_comment(self, feed_id: str, content: str) -> bool:
        """通过真实页面输入框发表冷启动评论"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()

                profile_url = f"https://www.xiaohongshu.com/user/profile/{self.profile_id}"
                page.goto(profile_url, wait_until="networkidle")
                page.wait_for_timeout(2000)

                note_items = page.query_selector_all("section.note-item, .note-item")
                target_card = None
                for el in note_items:
                    if feed_id and feed_id in (el.inner_html() or ""):
                        target_card = el
                        break
                if not target_card and note_items:
                    target_card = note_items[0]

                if not target_card:
                    browser.close()
                    return False

                target_card.click()
                page.wait_for_timeout(2500)

                # 小红书评论框未激活时是 .inner-when-not-active，激活后是 #content-textarea
                trigger_el = page.query_selector('.inner-when-not-active, .not-active')
                if trigger_el:
                    trigger_el.click()
                    page.wait_for_timeout(600)
                
                input_el = page.query_selector('#content-textarea, .content-input, [contenteditable="true"]')
                if input_el:
                    input_el.click()
                    page.wait_for_timeout(300)
                    page.keyboard.type(content, delay=30)
                    page.wait_for_timeout(800)

                    send_btn = page.query_selector(".submit, .send-btn, button:has-text('发送')")
                    if send_btn:
                        send_btn.click()
                        page.wait_for_timeout(2500)
                        logger.info(f"CDP successfully posted seed comment to {feed_id}")
                        page.keyboard.press("Escape")
                        browser.close()
                        return True

                browser.close()
                return False
        except Exception as e:
            logger.error(f"CDP post_seed_comment error: {e}")
            return False
