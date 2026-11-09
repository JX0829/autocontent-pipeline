#!/usr/bin/env bash
# GATE-2 · 台账幂等验收（spec_enhancement.md §5）
#
# 准入：T2.1–T2.6 全部 ✅
# 核心断言：同一 dedupe_key 连续投递 3 次，send_ledger 只能有 1 行。
# 附加：hook 级二次投递必须 already_sent；静默失败扫描不得新增违例。
#
# 用法：bash scripts/gate2.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0
TMP=$(mktemp -d)
DB="$TMP/gate2.db"
trap 'rm -rf "$TMP"' EXIT

echo "=== GATE-2 · 台账幂等验收 ==="

# 1) DB 层：同一 dedupe_key 连claim 3 次
OUT=$(SQLITE_DB_PATH="$DB" python3 - "$DB" <<'PY'
import sys
from src.db import PipelineDB
db = PipelineDB(sys.argv[1])
res = [db.claim_send_slot("k_test", "t1", "feishu", "oc_x", "/tmp/v.mp4") for _ in range(3)]
print(res)
import sqlite3
c = sqlite3.connect(sys.argv[1])
n = c.execute("select count(*) from send_ledger where dedupe_key='k_test'").fetchone()[0]
print(n)
PY
)
RES=$(echo "$OUT" | sed -n 1p)
CNT=$(echo "$OUT" | sed -n 2p)
if [ "$RES" = "[True, False, False]" ] && [ "$CNT" = "1" ]; then
  printf '  ✅ %-26s claim=%s rows=%s\n' "claim_send_slot ×3" "$RES" "$CNT"
else
  printf '  ❌ %-26s claim=%s rows=%s（期望 [True, False, False] / 1）\n' "claim_send_slot ×3" "$RES" "$CNT"
  fail=1
fi

# 2) hook 层：同一 payload 连投两次 → ok=true 然后 already_sent
HK=$(TMPDIR_WORK="$TMP" AUTOCONTENT_DATA_DIR="$TMP/data" SQLITE_DB_PATH="$DB" python3 - <<'PY'
import json, os, subprocess, sys
from pathlib import Path
tmp = Path(os.environ["TMPDIR_WORK"])
video = tmp / "v.mp4"
subprocess.run(["ffmpeg","-y","-v","error","-f","lavfi","-i","testsrc=size=640x360:rate=25:duration=2",
                "-c:v","libx264","-pix_fmt","yuv420p",str(video)], capture_output=True)
payload = {"task_id":"gate2","run_root":str(tmp),"step_id":"dispatch",
           "artifacts":{"video_16_9":"v.mp4"},
           "context":{"account":"douyin-tech-main","channel":"feishu","content_hash":"g"*64}}
def run():
    r = subprocess.run([sys.executable,"scripts/hooks/send_ledger.py"], input=json.dumps(payload),
                       capture_output=True, text=True)
    line = [l for l in r.stdout.strip().splitlines() if l.strip()]
    return json.loads(line[-1]) if line else {"ok":None,"reason":r.stderr[-120:]}
a, b = run(), run()
print(json.dumps({"first": a.get("ok"), "second": b.get("ok"), "reason": b.get("reason")}, ensure_ascii=False))
PY
)
FIRST=$(echo "$HK" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['first'],d['second'],d['reason'])" 2>/dev/null)
if [ "$FIRST" = "True False already_sent" ]; then
  printf '  ✅ %-26s first=True second=False already_sent\n' "hook 二次投递"
else
  printf '  ❌ %-26s %s\n' "hook 二次投递" "$FIRST"
  fail=1
fi

# 3) 静默失败扫描（T2.5）：只报新增
if python3 scripts/audit_silent_failure.py --json >/dev/null 2>&1; then
  printf '  ✅ %-26s 新增违例 0\n' "静默失败扫描"
else
  printf '  ❌ %-26s 存在新增静默失败反模式\n' "静默失败扫描"
  python3 scripts/audit_silent_failure.py | tail -5 | sed 's/^/       /'
  fail=1
fi

# 4) hook 引擎自检（T2.1–T2.6 全部 hook）
if python3 -m src.pipeline.hooks --selftest >/dev/null 2>&1; then
  printf '  ✅ %-26s SELFTEST PASS\n' "hook 引擎自检"
else
  printf '  ❌ %-26s 自检失败\n' "hook 引擎自检"
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "GATE-2 PASS"
else
  echo "GATE-2 FAIL —— 回退 T2.2 检查 send_ledger 主键是否生效"
fi
exit "$fail"
