#!/usr/bin/env bash
# GATE-3 · 端到端不重发验收入口
# 用法: bash scripts/gate3.sh [--keep]
set -uo pipefail
cd "$(dirname "$0")/.."

# 后台/非交互 shell 的 "$PY" 常解析到无项目依赖的 conda 解释器，
# 显式指定带依赖的解释器（可用 PYTHON=... 覆盖）。
PY="${PYTHON:-/opt/homebrew/opt/python@3.14/bin/python3.14}"
if ! "$PY" -c "import openai, edge_tts, lark_oapi" >/dev/null 2>&1; then
  echo "解释器缺少项目依赖: $PY" >&2
  echo "请指定 PYTHON=/path/to/python（需已安装 requirements.txt）" >&2
  exit 78
fi

exec "$PY" scripts/_gate3_e2e.py "$@"
