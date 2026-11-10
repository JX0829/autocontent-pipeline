import json
from typing import Dict, Any, Optional


class MissingChatIdError(RuntimeError):
    """飞书会话 ID 未配置。

    T3.2-R2：严禁把真实 open_id/chat_id 写成代码兜底 —— 缺失必须显式失败，
    否则消息会静默发到某个写死的人那里（或发不出去还没人知道）。
    """


# Card 2.0 的 schema 版本号只在这里出现一次（T3.2-R3：结构转换必须待在 card_builder 内）
CARD2_SCHEMA = "2.0"


class FeishuCardBuilder:
    """
    飞书交互式卡片构造器
    用于生成短视频待审卡片（含封面、标题、话题、文案、分镜简述及一键批准/驳回操作按钮）
    """
    
    @staticmethod
    def build_approval_card(
        task_id: str,
        title: str,
        topics: list[str],
        content_text: str,
        resource_keyword: str,
        fact_summary: str,
        duration_sec: float,
        video_size_mb: float,
        template_id: Optional[str] = None,
        status: str = "PENDING_APPROVAL",
        rejection_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        构建带有交互按钮的审批卡片 JSON（**Card 2.0**）

        2026-11-10 从 1.0 升级：`div`/`lark_md`/`action`/`note` 全部换成 2.0 的
        `markdown` / `column_set` / `behaviors`，解决移动端窄栏与按钮回调丢失问题。
        按钮 value 契约保持不变（action + task_id），后台处理逻辑无需改动。
        """
        topic_str = " ".join([f"#{t}" if not t.startswith("#") else t for t in topics])

        status_badges = {
            "PENDING_APPROVAL": ("🟠 待人工审核", "orange"),
            "APPROVED": ("🟢 审核通过 · 待上传草稿", "green"),
            "MANUAL_PUBLISHING": ("📱 已提取素材 · 待手机发布", "blue"),
            "PUBLISHED": ("✅ 手机发布完成", "green"),
            "REJECTED": ("🔴 已驳回", "red"),
        }
        badge_text, _badge_color = status_badges.get(status, ("⚪ 未知状态", "grey"))

        template_display_map = {
            "editorial_news": "T1 · 瑞士先锋报刊风 (Editorial Paper)",
            "cupertino_glass": "T2 · Apple 毛玻璃流光风 (Cupertino Glass)",
            "neo_brutalism": "T3 · 多巴胺波普粗野风 (Neo-Brutalism Pop)",
            "notion_craft": "T4 · Notion 暖调手账风 (Notion Warm Craft)",
            "linear_obsidian": "T5 · Linear 钛黑流光风 (Linear Obsidian)",
        }
        tpl_name = template_display_map.get(template_id or "", template_id or "未指定")

        elements: list[Dict[str, Any]] = [
            {"tag": "markdown", "content": (
                f"**状态**: {badge_text}  |  **时长**: `{duration_sec:.1f}s`  |  "
                f"**视频大小**: `{video_size_mb:.2f} MB`\n"
                f"🎨 **套用模板**: **`{tpl_name}`**\n**任务 ID**: `{task_id}`")},
            {"tag": "hr"},
            {"tag": "markdown", "content": (
                f"📌 **视频标题**:\n**{title}**\n\n🏷️ **话题标签**: {topic_str}")},
            {"tag": "markdown", "content": f"📝 **核心正文/文案**:\n{content_text}"},
            {"tag": "markdown", "content": (
                f"<font color='grey'>💡 事实核查依据: {fact_summary}</font>")},
        ]

        if status == "REJECTED" and rejection_reason:
            elements.append({
                "tag": "markdown",
                "content": f"⚠️ **驳回原因**: {rejection_reason}",
            })

        def _btn(label: str, action: str, btype: str) -> Dict[str, Any]:
            return {
                "tag": "column", "width": "auto",
                "elements": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": btype,
                    "behaviors": [{"type": "callback",
                                   "value": {"action": action, "task_id": task_id}}],
                }],
            }

        # 仅在待审批状态下展示操作按钮：二合一极简收敛（批准发布并分发 + 驳回）
        if status == "PENDING_APPROVAL":
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": ("🚀 **动态多平台分发矩阵**: `📺 B站(私密草稿)` · "
                            "`📲 微信视频号(公开分发)`"),
            })
            elements.append({
                "tag": "column_set",
                "columns": [_btn("✅ 批准发布并分发", "APPROVE_AND_DISPATCH", "primary"),
                            _btn("❌ 驳回重写", "REJECT_TASK", "danger")],
            })
        elif status == "MANUAL_PUBLISHING":
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "column_set",
                "columns": [_btn("✅ 手机发布已完成", "COMPLETE_MANUAL_PUBLISH", "primary")],
            })

        return {
            "schema": CARD2_SCHEMA,
            "config": {"update_multi": True},
            "header": {
                "template": ("blue" if status == "PENDING_APPROVAL"
                             else ("green" if status == "APPROVED" else "red")),
                "title": {
                    "tag": "plain_text",
                    "content": f"🎬 短视频待审工作台 · {title[:20]}",
                },
            },
            "body": {"elements": elements},
        }

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
