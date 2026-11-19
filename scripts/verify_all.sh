#!/usr/bin/env bash
# 全量验收入口：把分散的 GATE 串成一条命令，提交前/改动后跑一次
#
# 用法：
#   bash scripts/verify_all.sh            # 快速档（约 1~2 分钟）：GATE-1 + GATE-2 + GATE-5 + 硬编码扫描
#   bash scripts/verify_all.sh --full     # 完整档（约 12 分钟，含 Remotion 真实渲染的 GATE-3）
#   bash scripts/verify_all.sh --fast     # 极速档（约 10 秒）：只跑静态扫描，适合 pre-commit
#
# 退出码：0 全绿；非 0 有失败项（打印失败清单）
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-/opt/homebrew/opt/python@3.14/bin/python3.14}"
export PYTHON="$PY"   # 传给 gate1/2/3.sh：它们默认 python3，后台可能是缺依赖的 conda
MODE="normal"
case "${1:-}" in
  --full) MODE="full" ;;
  --fast) MODE="fast" ;;
  "") ;;
  *) echo "未知参数: $1（可用 --full / --fast）" >&2; exit 2 ;;
esac

PASS=(); FAIL=()

run() {
  local name="$1"; shift
  printf '\n──────── %s ────────\n' "$name"
  if "$@"; then
    PASS+=("$name")
  else
    FAIL+=("$name")
  fi
}

# ---------- 极速档：静态扫描（无渲染、无网络）----------
run "任务契约回归"        "$PY" scripts/test_task_contracts.py
run "课文故事 M1 契约"    "$PY" scripts/test_textbook_story.py
run "素材库 manifest 校验" "$PY" scripts/verify_assets.py
run "模板路由契约"        "$PY" scripts/test_template_routing.py
run "锐评四手法门禁"      "$PY" scripts/test_creator_take_guard.py
run "横版衍生素材契约"    "$PY" scripts/test_landscape_assets.py
run "水墨模板契约"        "$PY" scripts/test_ink_template.py
run "fact_summary 截断契约" "$PY" scripts/test_schema_truncation.py
run "物料硬约束(绑定/自证/审计)" "$PY" scripts/test_material_binding.py
run "静默失败扫描"        "$PY" scripts/audit_silent_failure.py
run "渠道无头巡检"        "$PY" scripts/audit_channels_headful.py
run "视频号稳定性(复用/扫码/门禁)" "$PY" scripts/test_channels_stability.py
run "RUNBOOK 命令自检"    "$PY" scripts/verify_runbook.py
run "竖版安全边距巡检"    "$PY" scripts/audit_safe_area.py
run "封面片头 0.5s"       "$PY" scripts/test_cover_prepend.py
run "封面 3:4 不裁字"      "$PY" scripts/test_cover_34_safe.py
run "封面规范(全模板)"    "$PY" scripts/audit_cover_standard.py
run "素材轮换(随机)"      "$PY" scripts/test_asset_rotation.py
run "seen-before-send"    "$PY" scripts/audit_silent_failure.py --check seen-before-send
run "封面硬编码扫描"      "$PY" scripts/check_cover_hardcode.py
run "发布入口收口审计"    "$PY" scripts/audit_publish_entrypoints.py
run "卡片 Card2.0 契约"   "$PY" scripts/test_card_builder.py
run "发布台账语义"        "$PY" scripts/test_publish_ledger.py
run "发布结论唯一(一张卡)" "$PY" scripts/test_publish_report.py
run "视频号话题真生效"     "$PY" scripts/test_channels_topics_contract.py
run "抖音话题真生效"       "$PY" scripts/test_douyin_topics_contract.py
run "发布凭证回查"        "$PY" scripts/test_publish_evidence.py
run "僵尸 RUNNING 收尸"   "$PY" scripts/test_stale_runs.py
run "cron pin 巡检"       "$PY" scripts/audit_cron_pin.py
run "封面回读中心84%"     "$PY" scripts/test_cover_center_hash.py

if [ "$MODE" != "fast" ]; then
  run "GATE-1 声明层契约"   bash scripts/gate1.sh
  run "GATE-2 台账幂等"     bash scripts/gate2.sh
  run "模型切换自检"        "$PY" scripts/model_switch_check.py --dry-run
fi

if [ "$MODE" = "full" ]; then
  run "GATE-3 端到端不重发" bash scripts/gate3.sh
fi

printf '\n════════════════ 汇总 ════════════════\n'
for p in "${PASS[@]:-}"; do [ -n "$p" ] && echo "  ✅ $p"; done
for f in "${FAIL[@]:-}"; do [ -n "$f" ] && echo "  ❌ $f"; done

if [ ${#FAIL[@]} -gt 0 ]; then
  echo
  echo "VERIFY FAIL —— ${#FAIL[@]} 项失败：${FAIL[*]}"
  exit 1
fi

case "$MODE" in
  fast) echo; echo "VERIFY PASS（极速档，未跑 GATE-1/2/3）" ;;
  full) echo; echo "VERIFY PASS（完整档，含 GATE-3 端到端）" ;;
  *)    echo; echo "VERIFY PASS（快速档，未跑 GATE-3；需要端到端请加 --full）" ;;
esac
