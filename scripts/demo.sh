#!/usr/bin/env bash
# DevOpsAgent 一期演示：绿灯 / 黄灯 / 红灯
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"
SECRET="${WEBHOOK_SECRET:-dev-webhook-secret}"

json() { curl -sS -H "Content-Type: application/json" -H "X-Webhook-Secret: $SECRET" "$@"; }

wait_status() {
  local id="$1"
  local want="$2"
  local n=0
  while [[ $n -lt 60 ]]; do
    local st
    st=$(curl -sS "$BASE/api/v1/tickets/$id" | python3 -c "import sys,json; print(json.load(sys.stdin)['ticket']['status'])")
    echo "  ticket $id -> $st"
    if [[ "$st" == "$want" ]]; then return 0; fi
    if [[ "$st" == "escalated" && "$want" != "escalated" ]]; then return 1; fi
    sleep 1
    n=$((n+1))
  done
  return 1
}

echo "== health =="
curl -sS "$BASE/health"; echo

echo "== 绿灯：CPU 飙高 → 滚动重启 =="
GREEN=$(json -X POST "$BASE/api/v1/webhooks/zabbix" -d '{
  "event_id":"demo-green-'"$(date +%s)"'",
  "asset_id":"ast-order-app-01",
  "trigger_name":"CPU usage > 85% for 5 minutes",
  "demo_scenario":"green"
}')
echo "$GREEN"
GID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['ticket']['id'])" "$GREEN")
wait_status "$GID" recovered

echo "== 黄灯：复制延迟 → 待审批 =="
YELLOW=$(json -X POST "$BASE/api/v1/webhooks/zabbix" -d '{
  "event_id":"demo-yellow-'"$(date +%s)"'",
  "asset_id":"ast-order-db-01",
  "trigger_name":"MySQL replication lag too high",
  "demo_scenario":"yellow"
}')
echo "$YELLOW"
YID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['ticket']['id'])" "$YELLOW")
wait_status "$YID" pending_approval
echo "== 批准黄灯任务 =="
curl -sS -X POST "$BASE/api/v1/tickets/$YID/approve" -H "Content-Type: application/json" \
  -d '{"approver":"王五","comment":"同意主备切换"}'
echo
wait_status "$YID" recovered

echo "== 红灯：未知崩溃 → 升级且不执行 =="
RED=$(json -X POST "$BASE/api/v1/webhooks/zabbix" -d '{
  "event_id":"demo-red-'"$(date +%s)"'",
  "asset_id":"ast-order-app-02",
  "trigger_name":"mystery native crash",
  "demo_scenario":"red"
}')
echo "$RED"
RID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['ticket']['id'])" "$RED")
wait_status "$RID" escalated

echo "== 日报 =="
curl -sS "$BASE/api/v1/reports/daily"; echo
echo "演示完成。"
