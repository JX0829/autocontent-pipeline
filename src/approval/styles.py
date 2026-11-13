"""审批卡片样式库 —— **样式与审批 provider 绑定**。

为什么要单独一层：
  卡片长什么样是渠道的事，不是流程的事。飞书的卡片结构换一版，
  不该动 `exec_approval`、不该动 registry、更不该动别的渠道。

绑定方式（`pipeline.yaml`）::

    providers:
      approval.feishu:
        kind: approval
        profile:
          card_style: approval_v2      # ← 样式名，provider 启动时解析成下面的函数

新增一个样式 = 在 `STYLES` 里加一个函数，然后在 pipeline.yaml 里改字符串。
样式函数只负责"长什么样"，数据（标题、平台矩阵、提示语）由 provider 组织好传进来。
"""
from __future__ import annotations

from typing import Any, Callable

from src.feishu.card_builder import FeishuCardBuilder

StyleFn = Callable[..., dict[str, Any]]


def _header(title: str, template: str) -> dict[str, Any]:
    return {"title": {"tag": "plain_text", "content": title}, "template": template}


def style_approval_v2(
    run_id: str,
    title: str,
    targets: list[dict[str, Any]] | None = None,
    *,
    delivered: bool = True,
    header_template: str = "blue",
    show_publish_matrix: bool = True,
    note: str = "",
    desc: str = "",
) -> dict[str, Any]:
    """完整版：标题 + 正文 + 任务 + 发布矩阵 + 两个按钮（默认样式）。"""
    card = FeishuCardBuilder.build_approval_card2(
        run_id=run_id, title=title,
        targets=(targets or []) if show_publish_matrix else [],
        delivered=delivered, note=note, desc=desc)
    # 卡头用**视频标题**，run_id 留在正文里 —— 一眼能看出是哪条片要审
    head = title.strip() if title and title.strip() else run_id
    card["header"] = _header(f"【待审批】{head}", header_template)
    return card


def style_approval_minimal(
    run_id: str,
    title: str,
    targets: list[dict[str, Any]] | None = None,
    *,
    delivered: bool = True,
    header_template: str = "grey",
    show_publish_matrix: bool = False,
    note: str = "",
    desc: str = "",
) -> dict[str, Any]:
    """极简版：标题 + 详情两个代码块，适合群里消息很多、不想刷屏的场景。"""
    _desc = " ".join(str(desc or "").split())
    if len(_desc) > 400:
        _desc = _desc[:399] + "…"
    lines = [f"```\n{title or run_id}\n```"]
    if _desc:
        lines.append(f"```\n{_desc}\n```")
    lines.append(f"`{run_id}`")
    if note:
        lines.append(note)
    lines.append("回复 **approve** 发布 / **reject** 放弃")
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": _header(f"【待审批】{title.strip() if title and title.strip() else run_id}",
                          header_template),
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {
                    "tag": "column_set",
                    "columns": [
                        {"tag": "column", "width": "auto", "elements": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 确认发布"},
                            "type": "primary",
                            "behaviors": [{"type": "callback",
                                           "value": {"decision": "approve", "run_id": run_id}}],
                        }]},
                        {"tag": "column", "width": "auto", "elements": [{
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 不发布"},
                            "type": "danger",
                            "behaviors": [{"type": "callback",
                                           "value": {"decision": "reject", "run_id": run_id}}],
                        }]},
                    ],
                },
            ]
        },
    }


STYLES: dict[str, StyleFn] = {
    "approval_v2": style_approval_v2,
    "approval_minimal": style_approval_minimal,
}

DEFAULT_STYLE = "approval_v2"


def get_style(name: str | None) -> StyleFn:
    """按名字取样式；名字非法必须显式失败，不能静默回落到默认。"""
    key = (name or DEFAULT_STYLE).strip()
    if key not in STYLES:
        raise KeyError(f"未知审批卡片样式: {key}（可选 {sorted(STYLES)}）")
    return STYLES[key]
