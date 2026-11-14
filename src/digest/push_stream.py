"""速报推送入口（SPEC-20261114 · T6/R5）。

从 `agents_context/xiao-pin/scripts/push_feishu_stream.py` 迁入飞书卡片发送 + 编排调用，
cron 侧 `run_push_stream.sh` 只保留一行调用本模块。

`--build-only` 打印速报正文（stdout 即正文，no_agent 纯脚本可直发），不发飞书。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

PROJ = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_FILE = PROJ / ".env"
logger = logging.getLogger(__name__)


def get_env_creds(env_file: Optional[str | Path] = None):
    """凭据：环境变量优先，其次读项目 .env（不打印任何值）。"""
    path = Path(env_file) if env_file else DEFAULT_ENV_FILE
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not app_id and line.startswith("FEISHU_APP_ID="):
                app_id = line.split("=", 1)[1].strip("\"' ")
            elif not app_secret and line.startswith("FEISHU_APP_SECRET="):
                app_secret = line.split("=", 1)[1].strip("\"' ")
    return app_id, app_secret


def _split_card(text: str):
    """正文里若带 CARD:{...} 协议块，抽出来当卡片头/按钮（与系统 CARD 协议兼容）。"""
    m = re.search(r"(?:^|\n)CARD:\s*(\{.*?\})(?:\s*|\n*)$", text, re.DOTALL)
    if not m:
        return None, text
    try:
        return json.loads(m.group(1)), text[:m.start()].strip()
    except Exception as exc:
        logger.warning(f"CARD 块 JSON 解析失败，按纯文本发送：{type(exc).__name__}: {exc}")
        return None, text


def send_stream_card(client, receive_id: str, text: str, receive_id_type: str = "chat_id") -> bool:
    """Card 2.0 + markdown 元素（Card 1.0 的 div/lark_md 会把 # 标题降级成纯文本）。"""
    card_data, body_text = _split_card(text)
    card_title = card_data.get("title") if card_data else "🚀 全网前沿 AI 实时技术与热点综合速报"
    card_template = card_data.get("template") if card_data else "blue"

    body_elements: list[Dict[str, Any]] = [{"tag": "markdown", "content": body_text}]
    if card_data and card_data.get("actions"):
        buttons = [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": a.get("label", "确认")},
            "type": a.get("type", "default"),
            "value": {"hermes_decision_card": True, "label": a.get("label"),
                      "reply_text": a.get("action")},
        } for a in card_data["actions"]]
        body_elements.append({"tag": "action", "actions": buttons})

    card_payload: Dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": card_title}, "template": card_template},
        "body": {"elements": body_elements},
    }

    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    req = CreateMessageRequest.builder() \
        .receive_id_type(receive_id_type) \
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("interactive")
            .content(json.dumps(card_payload, ensure_ascii=False))
            .build()
        ).build()
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"Error sending card: code={resp.code}, msg={resp.msg}", file=sys.stderr)
        return False
    return True


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="速报编排 + 飞书推送")
    ap.add_argument("--chat-id", default="oc_c3e01371d3bc916ef3f2af8b904eb785")
    ap.add_argument("--config", default=None)
    ap.add_argument("--build-only", action="store_true", help="只打印正文，不发飞书")
    ap.add_argument("--dry-run", action="store_true", help="不写去重库/快照")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(PROJ))
    from src.digest import config as digest_config
    from src.digest.orchestrator import build_unified_hotspot_digest

    text, snapshot = build_unified_hotspot_digest(digest_config.load(args.config),
                                                  writing=not args.dry_run)
    if args.build_only:
        print(text)
        print(f"\n[Info: build-only, {len(snapshot)} items]")
        return 0

    app_id, app_secret = get_env_creds()
    if not app_id or not app_secret:
        print("Missing FEISHU_APP_ID / FEISHU_APP_SECRET", file=sys.stderr)
        return 1

    import lark_oapi as lark
    client = lark.Client.builder().app_id(app_id).app_secret(app_secret).build()
    ok = send_stream_card(client, args.chat_id, text)
    print("ALL_STREAMS_SENT_SUCCESS" if ok else "FAILED_TO_SEND_STREAM")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
