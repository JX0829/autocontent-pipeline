#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带校验的视频投递器（替代 MEDIA: 协议投递视频）。

为什么不用 MEDIA:：
- Hermes 网关走 plugins/platforms/feishu/adapter.py 的 `send_video` → msg_type=media。
  实测该路径发出的消息在飞书侧无法被 API 回读（`msg_type=nonsupport` / get message 500），
  因此**无法验证用户实际收到的是哪一条视频**，历史上连续 4 次出现「收到的是上一个视频」。
- 本脚本直接调用 im/v1/files 上传 + im/v1/messages 发 media 消息，显式指定唯一文件名，
  并把 file_key / message_id 打印出来，作为可核对凭证。

用法：
  python3 scripts/send_video_verified.py --video path1.mp4 --video path2.mp4 [--chat oc_xxx] [--label run-id]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = "https://open.feishu.cn"
DEFAULT_CHAT = "oc_c3e01371d3bc916ef3f2af8b904eb785"
ENV_PATH = pathlib.Path.home() / ".hermes/profiles/xiao-pin/.env"


def load_cfg() -> dict:
    cfg = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET"):
        cfg.setdefault(k, os.getenv(k, ""))
    return cfg


def get_token(cfg: dict) -> str:
    req = urllib.request.Request(
        f"{BASE}/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": cfg["FEISHU_APP_ID"], "app_secret": cfg["FEISHU_APP_SECRET"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["tenant_access_token"]


def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(path: str) -> tuple[int, int, float]:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    ).stdout.strip().split(",")
    w, h, d = (out + ["0", "0", "0"])[:3]
    return int(float(w)), int(float(h)), float(d)


def upload(path: str, display_name: str, token: str) -> dict:
    boundary = "----hermesvideo"
    body = b""
    for k, v in (("file_type", "mp4"), ("file_name", display_name)):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{display_name}\"\r\n"
             f"Content-Type: video/mp4\r\n\r\n").encode()
    body += open(path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/open-apis/im/v1/files", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read())
    except urllib.error.HTTPError as exc:
        return {"code": -1, "error": exc.read().decode()[:300]}


def send_media(file_key: str, chat: str, token: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=json.dumps({"receive_id": chat, "msg_type": "media",
                         "content": json.dumps({"file_key": file_key})}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as exc:
        return {"code": -1, "error": exc.read().decode()[:300]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", action="append", required=True, help="视频绝对路径，可重复")
    ap.add_argument("--chat", default=DEFAULT_CHAT)
    ap.add_argument("--label", default="", help="前缀标签，如 run-id")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    if not cfg.get("FEISHU_APP_ID") or not cfg.get("FEISHU_APP_SECRET"):
        print(json.dumps({"ok": False, "error": "缺少 FEISHU_APP_ID/SECRET"}, ensure_ascii=False))
        return 2

    token = get_token(cfg)
    report = []
    for path in args.video:
        if not os.path.exists(path):
            report.append({"path": path, "ok": False, "error": "文件不存在"})
            continue
        w, h, dur = probe(path)
        orient = "portrait" if h > w else "landscape"
        digest = md5_of(path)
        display = f"{args.label + '_' if args.label else ''}{orient}_{w}x{h}_{digest[:8]}.mp4"
        item = {"local": path, "display_name": display, "md5": digest,
                "size": f"{w}x{h}", "duration": round(dur, 2), "orientation": orient}
        if args.dry_run:
            item["ok"] = True
            report.append(item)
            continue
        up = upload(path, display, token)
        fk = (up.get("data") or {}).get("file_key")
        item["upload_code"] = up.get("code")
        item["file_key"] = fk
        if not fk:
            item["ok"] = False
            item["error"] = up.get("error") or up.get("msg")
            report.append(item)
            continue
        snd = send_media(fk, args.chat, token)
        item["send_code"] = snd.get("code")
        item["message_id"] = (snd.get("data") or {}).get("message_id")
        item["ok"] = snd.get("code") == 0
        report.append(item)

    print(json.dumps({"ok": all(x.get("ok") for x in report), "chat": args.chat, "items": report},
                     ensure_ascii=False, indent=2))
    return 0 if all(x.get("ok") for x in report) else 1


if __name__ == "__main__":
    sys.exit(main())
