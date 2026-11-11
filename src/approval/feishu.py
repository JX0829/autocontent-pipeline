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
import threading
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


# ── 卡片按钮回调（长连接）────────────────────────────────────────────
# 卡片按钮是 behaviors:[{"type":"callback"}]，点下去飞书会推 card.action.trigger。
# 没人消费时客户端直接弹错误码（用户看到的就是这个）。这里在**审批等待期间**
# 挂一个飞书长连接收事件，把裁决写成 dp（裁决文件），ask() 的循环本来就在读它。
# 不常驻、不起服务：ask() 返回即断开。
_CARD_STATE: dict[str, Any] = {"client": None, "thread": None, "run_id": "", "active": False}


def _card_ws_enabled() -> bool:
    """默认关闭：卡片按钮已改纯展示，没有可点的回调，开长连接也只是白等。

    以后若恢复带 behaviors 的按钮、并在开放平台开了「卡片回传交互 → 长连接」，
    把 FEISHU_CARD_WS 置 1 即可重新启用。
    """
    return (os.getenv("FEISHU_CARD_WS", "0").strip().lower()
            not in ("0", "false", "no", "off"))


def _card_value(payload: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """从 P2CardActionTrigger 里取出 (button value, context, operator)，兼容对象/字典两种形态。"""
    raw: Any = payload if isinstance(payload, dict) else getattr(payload, "d", None)
    if not isinstance(raw, dict):
        # SDK 对象形态：P2CardActionTrigger.d / .event.d
        raw = getattr(payload, "event", None)
        raw = getattr(raw, "d", None) if raw is not None else None
        if not isinstance(raw, dict):
            raw = {}
    evt = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    value = (evt.get("action") or {}).get("value") or {}
    ctx = evt.get("context") or {}
    op = evt.get("operator") or {}
    return (value if isinstance(value, dict) else {},
            ctx if isinstance(ctx, dict) else {},
            op if isinstance(op, dict) else {})


def _write_card_decision(dp: Path, decision: str, who: str, evidence_msg_id: str = "") -> None:
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dp.with_name(dp.name + ".tmp")
    tmp.write_text(json.dumps({
        "decision": decision, "by": who or "card",
        "evidence_msg_id": evidence_msg_id, "at": int(time.time()),
    }, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dp)          # 原子替换，避免 ask() 读到半截 JSON


def _make_card_handler(run_id: str, dp: Path, allowed: set[str],
                       response_cls: Any) -> Any:
    """构造 card.action.trigger 处理器（抽出来是为了能单测，不依赖真长连接）。"""

    def _toast(msg: str, kind: str = "info") -> Any:
        return response_cls({"toast": {"type": kind, "content": msg}})

    def _on_card(payload: Any) -> Any:
        value, ctx, op = _card_value(payload)
        rid = str(value.get("run_id") or "")
        decision = str(value.get("decision") or "").strip().lower()
        who = str(op.get("open_id") or op.get("user_id") or "")
        print(f"  [approval] 收到卡片动作：run_id={rid or '空'} decision={decision or '空'}"
              f" who={who or '空'}", flush=True)
        if not _CARD_STATE.get("active"):
            return _toast("本次审批已结束", "warning")
        if rid and rid != run_id:
            return _toast("这张卡属于别的审批单", "warning")
        if decision not in ("approve", "reject"):
            return _toast(f"按钮值无法识别：{decision or '空'}", "error")
        if allowed and who not in allowed:
            return _toast("你没有审批权限", "error")
        if dp.exists():
            return _toast("这条已经裁过了", "warning")
        _write_card_decision(dp, decision, f"card:{who}",
                             str(ctx.get("open_message_id") or ""))
        print(f"  [approval] 卡片按钮 → {decision}（by {who or 'unknown'}）", flush=True)
        return _toast(f"已记录：{decision}", "success")

    return _on_card


def _run_ws(client: Any) -> None:
    try:
        client.start()
    except Exception as exc:  # noqa: BLE001 — 线程里不能抛，否则审批等待会静默失效
        print(f"  [approval] 卡片长连接异常退出：{type(exc).__name__}: {exc}")


def _start_card_listener(run_id: str, dp: Path) -> bool:
    """审批等待期挂长连接收卡片按钮回调。返回是否已启动。"""
    if not _card_ws_enabled():
        print("  [approval] 卡片按钮为纯展示，裁决请回复 approve/reject")
        return False
    if _CARD_STATE.get("active"):
        return True
    app_id = os.getenv("FEISHU_APP_ID") or ""
    app_secret = os.getenv("FEISHU_APP_SECRET") or ""
    if not app_id or not app_secret:
        print("  [approval] 卡片回调跳过：缺 FEISHU_APP_ID / FEISHU_APP_SECRET")
        return False
    try:
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError as exc:
        print(f"  [approval] 卡片回调跳过：lark_oapi 不可用（{exc}）")
        return False

    _on_card = _make_card_handler(run_id, dp, _allowed_users(), P2CardActionTriggerResponse)

    try:
        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_card_action_trigger(_on_card)
                   .build())
        client = lark.ws.Client(app_id, app_secret, event_handler=handler,
                                log_level=lark.LogLevel.WARNING)
        thread = threading.Thread(target=_run_ws, args=(client,), daemon=True,
                                  name="feishu-card-ws")
        thread.start()
    except Exception as exc:  # noqa: BLE001 — 回调起不来要能退回回复路径
        print(f"  [approval] 卡片回调启动失败（退回回复路径）：{type(exc).__name__}: {exc}")
        return False

    _CARD_STATE.update({"client": client, "thread": thread, "run_id": run_id, "active": True})
    print("  [approval] 卡片回调就绪（飞书长连接），可直接点按钮裁决")
    return True


def _stop_card_listener() -> None:
    client = _CARD_STATE.get("client")
    _CARD_STATE.update({"client": None, "run_id": "", "active": False})
    if client is None:
        return
    try:
        # SDK 没有公开 stop()；_disconnect 是协程，且用的是 lark_oapi.ws.client 的模块级 loop，
        # 必须 run_coroutine_threadsafe 丢回那个 loop（直接 await/调用都会 "never awaited"）。
        import asyncio

        from lark_oapi.ws.client import loop as _ws_loop
        asyncio.run_coroutine_threadsafe(client._disconnect(), _ws_loop).result(timeout=5)
        print("  [approval] 卡片长连接已断开")
    except Exception as exc:      # noqa: BLE001 — 关不掉也不该影响审批结果
        print(f"  [approval] 卡片长连接关闭异常（忽略）：{type(exc).__name__}: {exc}")


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
    card_ws = _start_card_listener(run_id, dp)
    last_scan = 0.0
    try:
        return _wait_decision(svc, dp, msg_id, chat_id, since_ms, deadline, card_ws,
                              timeout)
    finally:
        if card_ws:
            _stop_card_listener()


def _wait_decision(svc, dp: Path, msg_id, chat_id: str, since_ms: int,
                   deadline: float, card_ws: bool, timeout: int = 0) -> dict[str, Any]:
    """等裁决：卡片文件 → 群回复，先到先得。"""
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
            "reason": f"等待审批超时（{timeout or '?'}s），按拒绝处理",
            "feishu_msg_id": msg_id, "card_ws": card_ws}
