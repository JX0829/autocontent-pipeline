"""feishu 审批渠道（T4.6）：发通知 → 等人工裁决 → 超时即拒绝。

裁决来源有两条，先到先得：
  1. **飞书回复扫描**（主路径，自包含）：轮询会话消息，找通知之后用户发的
     approve / reject 文本。这是生产路径——不需要额外的回调服务。
  2. **裁决文件**（辅助路径）：data/approvals/<run_id>.json，供外部系统/人工写入。

超时策略：默认 600s，可用 APPROVAL_TIMEOUT_SEC 覆盖；超时 → reject（不静默放行）。
只认 sender_type=user 的消息；可用 APPROVAL_ALLOWED_USERS（逗号分隔 open_id）限定审批人。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("approval.feishu")

# 平台 → 默认视频方向（这是平台规则，不是账号字面量；账号一律从 accounts.yaml 读）
PLATFORM_ASPECT: dict[str, str] = {
    "douyin": "vertical",
    "bilibili": "landscape",
    "channels": "vertical",
    "weixin": "vertical",
}


def _publish_targets() -> list[dict[str, Any]]:
    """从 config/accounts.yaml 读启用的账号，生成审批卡片展示的发布矩阵。

    严禁在代码里写死账号 ID（T4.2 契约：账号字面量零残留）。
    每个账号可用 publish_aspect / publish_with_title / publish_with_description /
    declare_aigc / declare_original 覆盖默认值。
    环境变量 APPROVAL_PUBLISH_TARGETS=off 可关闭展示。
    """
    if (os.getenv("APPROVAL_PUBLISH_TARGETS") or "").lower() in {"off", "0", "false"}:
        return []
    try:
        import yaml

        cfg = yaml.safe_load((PROJ / "config" / "accounts.yaml").read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — 配置读不到就不展示矩阵，不影响审批
        logger.debug(f"读取 accounts.yaml 失败，审批卡片不展示发布矩阵: {e}")
        return []

    targets: list[dict[str, Any]] = []
    for acc in cfg.get("accounts") or []:
        if not isinstance(acc, dict) or not acc.get("enabled"):
            continue
        platform = str(acc.get("platform") or "")
        targets.append({
            "provider": platform,
            "account": acc.get("id"),
            "aspect": acc.get("publish_aspect") or PLATFORM_ASPECT.get(platform, "vertical"),
            "with_title": bool(acc.get("publish_with_title", True)),
            "with_description": bool(acc.get("publish_with_description", True)),
            "declare_aigc": bool(acc.get("declare_aigc", acc.get("is_aigc", 0) == 1)),
            # B站用 no_reprint=1 表示原创自制，这里统一映射成 declare_original
            "declare_original": bool(acc.get("declare_original", acc.get("no_reprint", 0) == 1)),
        })
    return targets

PROJ = Path(__file__).resolve().parent.parent.parent
DECISION_DIR = PROJ / "data" / "approvals"

APPROVE_WORDS = {"approve", "a", "y", "yes", "通过", "同意", "批准", "ok", "okay"}
REJECT_WORDS = {"reject", "r", "n", "no", "拒绝", "驳回", "不通过", "取消"}


def _decision_path(run_id: str) -> Path:
    return DECISION_DIR / f"{run_id}.json"


def _allowed_users() -> set[str]:
    raw = os.getenv("APPROVAL_ALLOWED_USERS") or ""
    return {x.strip() for x in raw.split(",") if x.strip()}


def _scan_chat_reply(svc, chat_id: str, since_ms: int) -> dict[str, Any] | None:
    """在会话里找通知之后用户发的第一条 approve/reject 文本。"""
    try:
        from lark_oapi.api.im.v1 import ListMessageRequest
        resp = svc.client.im.v1.message.list(
            ListMessageRequest.builder()
            .container_id_type("chat").container_id(chat_id)
            .page_size(20).sort_type("ByCreateTimeDesc").build())
    except Exception as e:  # noqa: BLE001 — 扫描失败不该打断审批等待
        print(f"  [approval] 消息扫描异常（忽略本轮）: {e}")
        return None
    if getattr(resp, "code", -1) != 0 or not getattr(resp, "data", None):
        return None

    allowed = _allowed_users()
    for item in (resp.data.items or []):
        try:
            create_ms = int(getattr(item, "create_time", "0"))
        except (TypeError, ValueError):
            print(f"  [approval] 消息 create_time 非法，跳过该条: "
                  f"{getattr(item, 'create_time', None)!r}")
            continue
        if create_ms < since_ms:
            continue
        sender = getattr(item, "sender", None)
        if not sender or getattr(sender, "sender_type", "") != "user":
            continue
        open_id = str(getattr(sender, "id", "") or "")
        if allowed and open_id not in allowed:
            continue
        if getattr(item, "msg_type", "") != "text":
            continue
        try:
            text = str(json.loads(item.body.content).get("text", "")).strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if text in APPROVE_WORDS:
            return {"decision": "approve", "by": open_id,
                    "evidence_msg_id": item.message_id, "text": text}
        if text in REJECT_WORDS:
            return {"decision": "reject", "by": open_id,
                    "evidence_msg_id": item.message_id, "text": text}
    return None


def ask(run_id: str, artifacts: dict[str, Any], message: str,
        profile: dict[str, Any] | None = None) -> dict[str, Any]:
    from src.feishu.approval_service import FeishuApprovalService

    prof = profile or {}

    receive_id = os.getenv("FEISHU_APPROVAL_CHAT_ID") or ""
    if not receive_id:
        return {"decision": "reject", "decided_by": "config_missing",
                "reason": "缺 FEISHU_APPROVAL_CHAT_ID，无法发审批"}
    rec_type = "open_id" if receive_id.startswith("ou_") else "chat_id"

    dp = _decision_path(run_id)
    if dp.exists():
        dp.unlink()          # 清掉上一轮残留裁决，避免"旧 approve 放行新一轮"

    svc = FeishuApprovalService()

    # 审批消息用 Card 2.0（交互卡片）：把"将发布到哪些平台、各平台怎么发"写清楚，
    # 附两个按钮；决策仍以回复 approve / reject 为权威路径（按钮回调可选）。
    title = ""
    try:
        dp_art = artifacts.get("draft_script") if isinstance(artifacts, dict) else None
        if dp_art and Path(dp_art).exists():
            _d = json.loads(Path(dp_art).read_text(encoding="utf-8"))
            title = str(_d.get("title") or _d.get("short_title") or "")
    except Exception as e:  # noqa: BLE001 — 取标题失败不影响审批
        logger.debug(f"读取 draft_script 标题失败，卡片标题留空: {e}")

    since_ms = int(time.time() * 1000) - 5000      # 容忍 5s 时钟偏差
    msg_id = None
    chat_id = ""
    try:
        from src.approval.styles import DEFAULT_STYLE, get_style

        try:
            style_fn = get_style(str(prof.get("card_style") or DEFAULT_STYLE))
        except KeyError as e:                      # 样式名写错 → 显式降级到默认，不静默发空卡
            logger.warning(f"{e}；已回落到 {DEFAULT_STYLE}")
            style_fn = get_style(DEFAULT_STYLE)

        card = style_fn(
            run_id=run_id,
            title=title,
            targets=_publish_targets(),
            delivered=True,
            header_template=str(prof.get("header_template") or "blue"),
            show_publish_matrix=bool(prof.get("show_publish_matrix", True)),
        )
        msg_id = svc.send_approval_card(receive_id=receive_id,
                                        receive_id_type=rec_type, card_content=card)
        if msg_id:
            chat_id = receive_id if rec_type == "chat_id" else ""
    except Exception as e:  # noqa: BLE001 — 卡片失败必须降级到文本，不能卡住审批
        logger.warning(f"审批卡片发送失败，降级为文本: {e}")
        msg_id = None

    if not msg_id:
        text = f"【待审批】{run_id}\n回 approve / reject"
        msg_id, chat_id = svc.send_text_message_ex(
            receive_id=receive_id, receive_id_type=rec_type, text=text)
    if not msg_id:
        return {"decision": "reject", "decided_by": "feishu_send_failed",
                "reason": "审批通知发送失败"}
    if not chat_id:
        chat_id = receive_id if rec_type == "chat_id" else ""

    timeout = int(prof.get("timeout_sec") or os.getenv("APPROVAL_TIMEOUT_SEC") or "600")
    deadline = time.time() + timeout
    print(f"  [approval] 已发飞书审批（msg_id={msg_id}, chat={chat_id}），"
          f"等待回复，超时 {timeout}s")
    last_scan = 0.0
    while time.time() < deadline:
        # 1) 裁决文件（辅助路径，立即可读）
        if dp.exists():
            try:
                data = json.loads(dp.read_text(encoding="utf-8"))
                decision = str(data.get("decision") or "").lower()
                if decision in ("approve", "reject"):
                    return {"decision": decision,
                            "decided_by": f"feishu_file:{data.get('by', '?')}",
                            "feishu_msg_id": msg_id,
                            "evidence_msg_id": data.get("evidence_msg_id")}
            except json.JSONDecodeError as e:
                print(f"  [approval] 裁决文件格式非法，忽略并继续等待: {dp.name} ({e})")
        # 2) 飞书回复扫描（主路径），每 3s 一次，避免打爆 API
        if chat_id and time.time() - last_scan >= 3:
            last_scan = time.time()
            hit = _scan_chat_reply(svc, chat_id, since_ms)
            if hit:
                return {"decision": hit["decision"],
                        "decided_by": f"feishu_reply:{hit['by']}",
                        "feishu_msg_id": msg_id,
                        "evidence_msg_id": hit["evidence_msg_id"],
                        "reply_text": hit["text"]}
        time.sleep(2)

    return {"decision": "reject", "decided_by": "feishu_timeout",
            "reason": f"等待审批超时（{timeout}s），按拒绝处理", "feishu_msg_id": msg_id}
