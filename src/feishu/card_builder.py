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
            # 纯展示：不再渲染 button。飞书卡片回调（card.action.trigger）未开通时，
            # 带 behaviors=callback 的按钮点下去只会弹错误码（用户看到 200830），
            # 所以这里只摆字样，裁决一律走群里回复。
            {"tag": "markdown", "content": "**✅ 确认发布** ｜ **❌ 不发布**"},
            {"tag": "markdown",
             "content": "回复 **approve** 确认发布 / **reject** 放弃（按钮仅作展示，不可点）"},
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
