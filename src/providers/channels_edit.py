#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频号「补填短标题」实现（SPEC-20261115-fix-short-title.md）。

## 关键结论：面板怎么可靠唤起（本次移交的难点）

被移交的猜想是「在卡片上 hover → 点 .opr 里的 .more-action → 点『修改描述和封面』」。
实测（CDP 9334，2026-11-15）这条路走不通，原因有三层：

1. **卡片不在主文档里**，在 `micro/content/post/list` 这个 content iframe 里
   （主文档只有 11 个字符的壳）。spec 第 2 节的 frame 判断是反的。
2. 每张卡片都渲染了一份菜单，20 张卡片 = 20 份 `.more-action` + 20 份
   「修改描述和封面」文本；`getBoundingClientRect().width > 0` 对**所有**副本都成立
   （它们是绝对定位铺开的），所以 Playwright 的可见性判定被完全骗过。
3. 即使拿到正确坐标、用**真实鼠标**（trusted 事件）点下去，`页面上的菜单项` 也只
   触发 `.edit-info` 那个**只读信息卡**（视频封面/视频描述/短标题，全是 div，没有输入框），
   真正的编辑入口不在菜单里。

真正可靠的入口是**直接调用卡片组件的 Vue 方法**（页面是 Vue 2，元素上有 `__vue__`）：

    const vm = card.querySelector('.post-feed-item 内的任意节点').__vue__;
    vm.enterEditPost(vm.post)      // → 跳 /platform/post/create（编辑态）

编辑态下的短标题输入框：

    input[placeholder*="短标题"]    // 占位符：「填写短标题有机会获得更多流量」

这条路不依赖 hover、不依赖菜单、不依赖 trusted 事件，一次就对。
（`handleShowEditInfoPost` 是那个只读信息卡，别用错。）
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("channels_edit")

CDP = "http://127.0.0.1:9334"
LIST_URL = "https://channels.weixin.qq.com/platform/post/list"
CONTENT_FRAME = "micro/content/post/list"
EDIT_FRAME = "micro/content/post/create"
SHORT_TITLE_PH = "短标题"
POST_LIST_API = "/cgi-bin/mmfinderassistant-bin/post/post_list"

#: 失败码（spec R4）
POST_NOT_FOUND = "POST_NOT_FOUND"
MODIFY_PANEL_UNAVAILABLE = "MODIFY_PANEL_UNAVAILABLE"
SHORT_TITLE_INPUT_NOT_FOUND = "SHORT_TITLE_INPUT_NOT_FOUND"
SHORT_TITLE_NOT_LANDED = "SHORT_TITLE_NOT_LANDED"
SHORT_TITLE_UNRESOLVED = "SHORT_TITLE_UNRESOLVED"
ALREADY_SET = "ALREADY_SET"
DESCRIPTION_CHANGED = "DESCRIPTION_CHANGED"
CDP_UNAVAILABLE = "CDP_UNAVAILABLE"


@dataclass
class PostItem:
    object_id: str
    create_time: int
    description: str
    short_title: str

    @property
    def desc_head(self) -> str:
        return (self.description or "").strip().split("\n")[0].strip()


def _frame(page, needle: str):
    for f in page.frames:
        if needle in f.url:
            return f
    return None


async def _wait_frame(page, needle: str, timeout: float = 25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = _frame(page, needle)
        if f is not None:
            return f
        await asyncio.sleep(0.5)
    return None


POST_LIST_JS = """async ({api, size, cur}) => {
  const r = await fetch(api, {method:'POST', credentials:'include',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({pageSize: size, currentPage: cur, timestamp: Date.now()})});
  const t = await r.text();
  return JSON.stringify({__status: r.status, __body: t});
}"""


async def fetch_post_list(page, size: int = 5, cur: int = 1) -> dict:
    """拉一页 post_list，**主帧失败自动退到 iframe**，并把失败说清楚。

    2026-11-20 实证：`/platform/post/list` 主帧正文只有 11 字符（「视频号 · 助手」），
    真正的内容在子 frame `micro/content/post/list` 里。**但 DOM 不是证据来源** ——
    post_list 接口本身在同一页面就返回 errCode=0 + 20KB JSON（主帧和 iframe 都能调通）。
    所以这里只认接口结果，不拿正文长度猜「有没有作品」。

    失败抛 ChannelsListError（带 status / errCode / 试过的 frame），
    绝不当成「列表为空」。
    """
    api = POST_LIST_API
    targets: list[tuple[str, object]] = [("main", page)]
    for f in getattr(page, "frames", []) or []:
        try:
            fu = f.url or ""
        except Exception:  # noqa: BLE001
            continue
        if "channels.weixin.qq.com" in fu and f is not page:
            targets.append((fu[:60], f))

    tried: list[str] = []
    last_err = ""
    for name, target in targets:
        try:
            raw = await target.evaluate(POST_LIST_JS, {"api": api, "size": size, "cur": cur})
            env = json.loads(raw)
            body = env.get("__body") or ""
            data = json.loads(body) if body else {}
            if data.get("errCode") in (0, None) and ((data.get("data") or {}).get("list") is not None):
                return data
            tried.append(f"{name}: status={env.get('__status')} errCode={data.get('errCode')} "
                         f"{str(data.get('errMsg'))[:40]}")
            last_err = f"errCode={data.get('errCode')} {data.get('errMsg')}"
        except Exception as exc:  # noqa: BLE001
            tried.append(f"{name}: {type(exc).__name__}: {str(exc)[:60]}")
            last_err = f"{type(exc).__name__}: {str(exc)[:60]}"
    raise ChannelsListError(f"post_list 调用失败（{last_err}）｜尝试：{tried}")


class ChannelsListError(RuntimeError):
    """post_list 拿不到数据（登录过期/接口变更/被风控），调用方必须按失败处理。"""


async def list_posts(page, pages: int = 3, page_size: int = 20) -> list[PostItem]:
    """拉 post_list（只读）。短标题在 desc.title[0].shortTitle。"""
    out: list[PostItem] = []
    for cur in range(1, pages + 1):
        data = await fetch_post_list(page, page_size, cur)
        if data.get("errCode") not in (0, None):
            logger.warning(f"post_list errCode={data.get('errCode')} {data.get('errMsg')}")
        items = ((data.get("data") or {}).get("list") or [])
        if not items:
            break
        for it in items:
            desc = it.get("desc") or {}
            titles = desc.get("title") or []
            st = ""
            if isinstance(titles, list) and titles:
                st = str(titles[0].get("shortTitle") or "")
            elif isinstance(titles, dict):
                st = str(titles.get("shortTitle") or "")
            out.append(PostItem(
                object_id=str(it.get("objectId") or it.get("exportId") or ""),
                create_time=int(it.get("createTime") or 0),
                description=str(desc.get("description") or ""),
                short_title=st.strip(),
            ))
    return out


async def open_edit_for(page, post: PostItem) -> str:
    """按描述首句定位卡片 → 调 Vue `enterEditPost` → 返回失败码或空串。"""
    f = await _wait_frame(page, CONTENT_FRAME)
    if f is None:
        return MODIFY_PANEL_UNAVAILABLE

    # 平台是 weui 分页器（.weui-desktop-pagination，一页 20 条）：先在第 1 页找，
    # 找不到再逐页翻（最多 6 页）。只滚动是没用的 —— 实测滚到底卡片数恒为 20。
    found = False
    used = ""
    match_js = """({want}) => {
      const items = [...document.querySelectorAll('.post-feed-item')];
      for (const c of items) {
        if (!(c.innerText || '').includes(want)) continue;
        let vm = null;
        [c, ...c.querySelectorAll('*')].forEach(e => { if (!vm && e.__vue__) vm = e.__vue__; });
        if (!vm || typeof vm.enterEditPost !== 'function') continue;
        vm.enterEditPost(vm.post);
        return true;
      }
      return false;
    }"""
    for page_no in range(1, 7):
        if page_no > 1:
            ok = await f.evaluate("""(n) => {
              const nums = [...document.querySelectorAll('.weui-desktop-pagination__num__')]
                .filter(e => (e.innerText || '').trim() === String(n) && e.getBoundingClientRect().width > 0);
              if (nums.length) { nums[0].click(); return true; }
              const nxt = [...document.querySelectorAll('.weui-desktop-pagination__nav')]
                .find(e => (e.innerText || '').includes('下一页'));
              if (nxt) { nxt.click(); return true; }
              return false;
            }""", page_no)
            if not ok:
                break
            await asyncio.sleep(2.6)
        for plen in (24, 18, 14, 10, 8):
            want = post.desc_head[:plen]
            if not want:
                continue
            if await f.evaluate(match_js, {"want": want}):
                found, used = True, want
                break
        if found:
            break

    if not found:
        logger.warning(f"卡片未命中（试过 24/18/14/10/8 字前缀）: {post.desc_head[:24]}")
        return POST_NOT_FOUND
    logger.info(f"卡片命中（{len(used)} 字前缀）: {used}")

    if await _wait_frame(page, EDIT_FRAME, timeout=20) is None:
        return MODIFY_PANEL_UNAVAILABLE
    await asyncio.sleep(2.0)
    return ""


async def set_short_title(page, value: str) -> tuple[str, str]:
    """在编辑页写短标题并回读。返回 (失败码或空串, 回读到的值)。"""
    f = await _wait_frame(page, EDIT_FRAME)
    if f is None:
        return MODIFY_PANEL_UNAVAILABLE, ""

    loc = f.locator('input[placeholder*="短标题"]').first
    try:
        if await loc.count() == 0:
            return SHORT_TITLE_INPUT_NOT_FOUND, ""
    except Exception:
        return SHORT_TITLE_INPUT_NOT_FOUND, ""

    await loc.click()
    await f.evaluate("""(sel) => { const e=document.querySelector(sel); if(e){e.value=''; e.dispatchEvent(new Event('input',{bubbles:true}));} }""",
                     'input[placeholder*="短标题"]')
    await loc.fill(value)
    await asyncio.sleep(0.5)
    got = (await loc.input_value() or "").strip()
    if got[:8] != value[:8]:
        return SHORT_TITLE_NOT_LANDED, got
    return "", got


async def submit_edit(page) -> str:
    """点「发表」保存修改（编辑态下即更新原作品，不新建）。"""
    f = await _wait_frame(page, EDIT_FRAME)
    if f is None:
        return MODIFY_PANEL_UNAVAILABLE
    btns = f.locator('button:has-text("发表"), [class*=btn]:has-text("发表")')
    n = await btns.count()
    for i in range(n):
        b = btns.nth(i)
        try:
            if (await b.inner_text()).strip() == "发表" and await b.is_visible():
                await b.click()
                await asyncio.sleep(5)
                return ""
        except Exception as exc:  # noqa: BLE001 — 单个按钮点击失败继续试下一个，但要留痕
            logger.debug("跳过按钮 #%s：%s: %s", i, type(exc).__name__, exc)
            continue
    return MODIFY_PANEL_UNAVAILABLE


# ───────────────────────────── 高层流程 ─────────────────────────────
def _run_desc_head(run_id: str) -> str:
    """从 run 目录取描述首句（发布时就是「标题 + 换行 + 正文」）。"""
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent.parent / "output"
    for d in sorted(root.glob(f"*/{run_id}"), reverse=True):
        for name in ("draft_script.json", "manifest.json", "publish.json"):
            f = d / name
            if not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 — 单个文件坏不影响其它候选，但要留痕
                logger.debug("跳过 %s：%s: %s", f.name, type(exc).__name__, exc)
                continue
            draft = data.get("draft") or data
            t = str(draft.get("title") or "").strip()
            if t:
                return t[:24]
    return ""


async def fix_short_titles(run_id: str = "", all_missing: bool = False,
                           dry_run: bool = False, override: str = "",
                           match: str = "") -> dict:
    """补短标题主流程。返回 {results: [...], summary: ...}，只读时 dry_run=True。"""
    from playwright.async_api import async_playwright
    from src.utils.text_sanitizer import pick_short_title

    report: dict = {"dry_run": dry_run, "results": []}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)

        posts = await list_posts(page, pages=3)
        if not posts:
            report["summary"] = CDP_UNAVAILABLE
            await page.close()
            return report

        if run_id or match:
            head = (match or _run_desc_head(run_id)).strip()
            if not head:
                report["results"].append({
                    "target": run_id, "error": POST_NOT_FOUND,
                    "hint": "run 产物已被 output/ 3 天清理，改用 --match '<描述片段>' 指定目标"})
                report["summary"] = POST_NOT_FOUND
                await page.close()
                return report
            targets = [x for x in posts if head in x.desc_head or head in x.description] or []
            if not targets:
                report["results"].append({
                    "target": head, "error": POST_NOT_FOUND,
                    "missing_sample": [{"desc_head": x.desc_head[:20], "ct": x.create_time}
                                       for x in posts if not x.short_title][:8]})
                report["summary"] = POST_NOT_FOUND
                await page.close()
                return report
            targets = targets[:1]
        elif all_missing:
            targets = [x for x in posts if not x.short_title]
        else:
            targets = []

        for post in targets:
            want = pick_short_title(post.desc_head, 16, override)
            item: dict = {"object_id": post.object_id, "desc_head": post.desc_head[:24],
                          "short_title_before": post.short_title, "want": want}
            if post.short_title:
                item["result"] = ALREADY_SET
                report["results"].append(item)
                continue
            if not want:
                item["result"] = SHORT_TITLE_UNRESOLVED
                report["results"].append(item)
                continue
            if dry_run:
                item["result"] = "DRY_RUN"
                report["results"].append(item)
                continue

            await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            err = await open_edit_for(page, post)
            if err:
                item["result"] = err
                report["results"].append(item)
                continue
            err2, landed = await set_short_title(page, want)
            item["landed"] = landed
            if err2:
                item["result"] = err2
                report["results"].append(item)
                continue
            err3 = await submit_edit(page)
            if err3:
                item["result"] = err3
                report["results"].append(item)
                continue

            # 回读自证：短标题命中 + 描述逐字不变
            await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)
            after = await list_posts(page, pages=2)
            hit = next((x for x in after if x.object_id == post.object_id), None)
            if hit is None:
                item["result"] = POST_NOT_FOUND
            elif hit.short_title.strip() != want:
                item["result"] = SHORT_TITLE_NOT_LANDED
                item["short_title_after"] = hit.short_title
            elif hit.description.strip() != post.description.strip():
                item["result"] = DESCRIPTION_CHANGED
            else:
                item["result"] = "OK"
                item["short_title_after"] = hit.short_title
            report["results"].append(item)

        ok = sum(1 for r in report["results"] if r.get("result") in ("OK", "DRY_RUN", ALREADY_SET))
        report["summary"] = f"{ok}/{len(report['results'])}"
        await page.close()
    return report
