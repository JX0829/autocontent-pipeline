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

# ---- 渲染前置检查（2026-11-13 踩过）------------------------------------
# Remotion 渲染需要 renderer/public/sfx/*.wav（BGM/音效）和 renderer/node_modules。
# 缺任何一个时，render 步只抛 CalledProcessError，GATE-3 会一次红 11 项、看不出根因。
# 新 worktree 最容易缺这两样（音效曾只在 chore 分支上，现已在库）。
missing=()
for f in bgm_loop.wav ding.wav glitch.wav typing.wav whoosh.wav; do
  [ -f "renderer/public/sfx/$f" ] || missing+=("renderer/public/sfx/$f")
done
[ -d renderer/node_modules ] || missing+=("renderer/node_modules")
if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ 渲染前置缺失（Remotion 会渲染失败，不是代码问题）:" >&2
  printf '   - %s\n' "${missing[@]}" >&2
  echo "   修复: git checkout -- renderer/public/sfx        # 音效已入库" >&2
  echo "         ln -sfn \$PWD/../autocontent-pipeline/renderer/node_modules renderer/node_modules" >&2
  exit 79
fi

exec "$PY" scripts/_gate3_e2e.py "$@"
