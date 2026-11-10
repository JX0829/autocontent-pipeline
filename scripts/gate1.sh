#!/usr/bin/env bash
# GATE-1 · 声明层与模型契约验收（spec_enhancement.md §5）
#
# 用途：P1 全部任务完成后的一键准入检查。任何一项失败即非零退出，禁止进入 P2。
# 用法：bash scripts/gate1.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

# 统一解释器：后台/CI 的 "$PY" 可能是缺依赖的 conda，必须可覆盖
PY="${PYTHON:-python3}"

fail=0
run() {
  local label="$1"; shift
  local out
  if out=$("$@" 2>&1); then
    printf '  ✅ %-28s %s\n' "$label" "$(echo "$out" | tail -1)"
  else
    printf '  ❌ %-28s EXIT=%s\n' "$label" "$?"
    echo "$out" | tail -5 | sed 's/^/       /'
    fail=1
  fi
}

echo "=== GATE-1 · 声明层与模型契约验收 ==="
run "bootstrap 运行时自举"   "$PY" scripts/bootstrap.py --dry-run --quiet
run "runner 干跑"            "$PY" -m src.pipeline.runner --material /dev/null --dry-run
run "契约结构校验"           "$PY" -c "from src.pipeline.contracts import load_pipeline,validate_structure as v;c=load_pipeline('pipeline.yaml');assert not v(c);print('STRUCT OK')"
run "模型切换自检"           "$PY" scripts/model_switch_check.py --dry-run
run "物料契约"               "$PY" -c "import json;s=json.load(open('manifest.schema.json'));assert s.get('anyOf');print('MANIFEST OK required=%d anyOf=%d' % (len(s['required']), len(s['anyOf'])))"
run "投料通道自检"           "$PY" -m src.pipeline.intake --selftest
run "hook 引擎自检"          "$PY" -m src.pipeline.hooks --selftest
run "配置面巡检"             "$PY" scripts/audit_config.py

echo
if [ "$fail" -eq 0 ]; then
  echo "GATE-1 PASS"
else
  echo "GATE-1 FAIL —— 退回对应任务，禁止进入 P2"
fi
exit "$fail"
