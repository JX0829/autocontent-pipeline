#!/usr/bin/env python3
"""
[DEPRECATED / 废弃声明]
scripts/run_feishu_worker.py

⚠️ 架构红线警告：
本外挂长连接 Worker 已废弃归档！
严禁以独立进程建立针对飞书应用的 WebSocket 长连接。
所有卡片交互与审批流转必须 100% 收拢由 Hermes 原生 Gateway (hermes gateway run) 独占托管，
以彻底根除双 Client 监听冲突、卡片事件随机分流与 362 超时报错。

历史实现已归档至: ~/.hermes/archives/cleanup_trash_20261030/run_feishu_worker.py
规范详见: references/feishu_websocket_exclusive_gateway.md
"""

import sys


def main():
    print(
        "❌ [CRITICAL ERROR] run_feishu_worker.py has been permanently deprecated.\n"
        "Do NOT start external WebSocket workers. All card interactions are exclusively handled by Hermes Gateway.\n"
        "See references/feishu_websocket_exclusive_gateway.md for details.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
