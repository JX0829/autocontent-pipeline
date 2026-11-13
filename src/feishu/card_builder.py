import json
from typing import Dict, Any, Optional


class MissingChatIdError(RuntimeError):
    """飞书会话 ID 未配置。

    T3.2-R2：严禁把真实 open_id/chat_id 写成代码兜底 —— 缺失必须显式失败，
    否则消息会静默发到某个写死的人那里（或发不出去还没人知道）。
    """


# Card 2.0 的 schema 版本号只在这里出现一次（T3.2-R3：结构转换必须待在 card_builder 内）
CARD2_SCHEMA = "2.0"

# ---------------------------------------------------------------- 发布回执文案
# 2026-11-11：发布结果只发这一张卡，摘要 ≤100 字。
# 之前没有回执模板，每次由 agent 现写长文（实测最长 8701 字），渠道多了没法看。
PLATFORM_CN = {"douyin": "抖音", "bilibili": "B站", "channels": "视频号"}
REASON_CN = {
    "LOGIN_EXPIRED": "登录过期",
    "PUBLISH_SIGNAL_MISSING": "未确认发布",
    "READBACK_NOT_FOUND": "回读不到作品",
    "ORIGINAL_CHECKBOX_NOT_FOUND": "原创勾选失败",
    "PUBLISHED_UNVERIFIED": "发布未证实",
    "CDP_UNAVAILABLE": "浏览器未起",
    "SUBMIT_UNKNOWN": "提交结果未知",
    "TIMEOUT": "超时",
}


def _short_reason(raw: Any) -> str:
    """错误码 → 8 字内中文；不截半个英文单词。"""
    raw = str(raw or "").strip().split("\n")[0]
    code = raw.split(":")[0].split("（")[0].strip()
    if code in REASON_CN:
        return REASON_CN[code]
    zh = "".join(ch for ch in raw if "\u4e00" <= ch <= "\u9fff")
    return zh[:8] or code[:8] or "失败"


def publish_receipt_text(report: Dict[str, Any], title: str = "", limit: int = 100) -> str:
    """把发布结果压成一行摘要（≤limit 字），不含卡片标题。"""
    ok = [PLATFORM_CN.get(str(p.get("platform")), str(p.get("platform")))
          for p in report.get("published") or []]
    bad = report.get("failed") or []
    skipped = report.get("skipped") or []

    if bad:
        names = [PLATFORM_CN.get(str(b.get("platform")), str(b.get("platform"))) for b in bad]
        head = "、".join(names[:2]) + (f" 等{len(names)}个" if len(names) > 2 else "")
        first = bad[0]
        line = (f"⚠️ {len(ok)}/{len(ok) + len(bad)} 成功｜{head}失败："
                f"{_short_reason(first.get('error') or first.get('message'))}")
    elif ok:
        line = f"✅ 已发布 {len(ok)}/{len(ok)}：{'·'.join(ok)}"
    elif skipped:
        line = f"⏭ 未发布（{len(skipped)} 项被门禁拦下）"
    else:
        line = "— 无平台结果"

    if title:
        head_t = title if len(title) <= 12 else title[:11] + "…"
        line = f"《{head_t}》{line}"
    return line if len(line) <= limit else line[: limit - 1] + "…"



class FeishuCardBuilder:
    """
    飞书交互式卡片构造器
    用于生成短视频待审卡片（含封面、标题、话题、文案、分镜简述及一键批准/驳回操作按钮）
    """
    
    @staticmethod
    # ================================================================
    # 审批用 Card 2.0（交互卡片，附在审批消息后）
    # ================================================================

    @staticmethod
    def build_approval_card2(
        run_id: str,
        title: str,
        targets: Optional[list[Dict[str, Any]]] = None,
        delivered: bool = True,
        note: str = "",
    ) -> Dict[str, Any]:
        """审批 Card 2.0：说明本次将发布到哪些平台、各平台怎么发，底部两个按钮。

        决策仍以「回复 approve / reject」为权威路径；按钮 value 里带 decision，
        后台若已订阅卡片回调可直接接住，两边不冲突。
        """
        lines = [f"**标题**：{title or '（无标题）'}", f"**任务**：`{run_id}`"]
        if delivered:
            lines.append("成片与封面已在上一条消息交付，请先看片再确认。")
        if targets:
            lines.append("")
            lines.append("**确认后将公开发布到**：")
            for t in targets:
                flags = ["竖版" if str(t.get("aspect")) == "vertical" else "横版"]
                if t.get("with_title"):
                    flags.append("带标题")
                if t.get("with_description"):
                    flags.append("带详情")
                if t.get("declare_aigc"):
                    flags.append("AI声明")
                if t.get("declare_original"):
                    flags.append("原创")
                lines.append(f"- **{t.get('provider')}** · " + " · ".join(flags))
        if note:
            lines.append("")
            lines.append(note)

        elements: list[Dict[str, Any]] = [
            {"tag": "markdown", "content": "\n".join(lines)},
            {"tag": "hr"},
            {"tag": "markdown", "content": "回复 **approve** 确认发布 / **reject** 放弃"},
            {
                "tag": "column_set",
                "columns": [
                    {
                        "tag": "column", "width": "auto",
                        "elements": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 确认发布"},
                            "type": "primary",
                            "behaviors": [{"type": "callback",
                                           "value": {"decision": "approve", "run_id": run_id}}],
                        }],
                    },
                    {
                        "tag": "column", "width": "auto",
                        "elements": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 不发布"},
                            "type": "danger",
                            "behaviors": [{"type": "callback",
                                           "value": {"decision": "reject", "run_id": run_id}}],
                        }],
                    },
                ],
            },
        ]
        return {
            "schema": CARD2_SCHEMA,
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": f"【待审批】{run_id}"},
                       "template": "blue"},
            "body": {"elements": elements},
        }

    # ================================================================
    # T3.2-R3：Card 2.0 构建（原 run_publish_worker._post_to_card2 下沉至此）
    # ================================================================

    # ================================================================
    # 发布回执卡（2026-11-11）：发布结果只发这一张，摘要 ≤100 字
    # ================================================================

    @staticmethod
    def build_publish_receipt(report: Dict[str, Any], title: str = "") -> Dict[str, Any]:
        """发布回执 Card 2.0：header 固定「发布回执」，正文一行摘要。"""
        return {
            "schema": CARD2_SCHEMA,
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "发布回执"},
                "template": "orange" if report.get("failed") else "green",
            },
            "body": {"elements": [{"tag": "markdown",
                                   "content": publish_receipt_text(report, title)}]},
        }

    @staticmethod
    def build_card2_from_post(post_payload: Dict[str, Any]) -> Dict[str, Any]:
        """把旧的 post 富文本 payload 转成 Card 2.0（interactive）。

        QA#8：飞书 post 的 `code_block` 在移动端已不渲染，但 API 仍返回 success。
        统一转成 markdown 围栏代码块，手机端可点一键复制。

        旧结构: {"zh_cn": {"title": str, "content": [[{tag,text|language}...]]}}
        """
        zh = post_payload.get("zh_cn", post_payload)
        title = zh.get("title", "")
        elements: list[Dict[str, Any]] = []
        for block in zh.get("content", []):
            for item in block:
                tag = item.get("tag")
                if tag == "text":
                    txt = (item.get("text") or "").strip("\n")
                    if txt:
                        elements.append({"tag": "markdown", "content": txt})
                elif tag == "code_block":
                    code = item.get("text") or ""
                    lang = item.get("language") or "plaintext"
                    elements.append({"tag": "markdown", "content": f"```{lang}\n{code}\n```"})
                elif tag == "markdown":
                    elements.append({"tag": "markdown", "content": item.get("content") or ""})
        return {
            "schema": CARD2_SCHEMA,
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title or "发布结果"}, "template": "blue"},
            "body": {"elements": elements},
        }

    @staticmethod
    def build_mobile_copy_card(
        task_id: str,
        short_title: str,
        full_body: str,
        *,
        heading: Optional[str] = None,
        lead: Optional[str] = None,
    ) -> Dict[str, Any]:
        """手机端发帖素材卡：标题与正文各一个围栏代码块，便于一键复制。

        直接产出 Card 2.0，**不再经过 post/code_block 中间结构**（QA#8 的根治）。
        """
        title = heading or f"📱 【手机端发帖纯净素材】（任务: {task_id}）"
        elements: list[Dict[str, Any]] = []
        if lead:
            elements.append({"tag": "markdown", "content": lead})
        elements.extend([
            {"tag": "markdown", "content": "📌 **标题（点击代码块一键复制）：**"},
            {"tag": "markdown", "content": f"```plaintext\n{short_title}\n```"},
            {"tag": "markdown", "content": "📝 **正文内容与标签（点击代码块一键复制）：**"},
            {"tag": "markdown", "content": f"```plaintext\n{full_body}\n```"},
        ])
        return {
            "schema": CARD2_SCHEMA,
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "body": {"elements": elements},
        }
