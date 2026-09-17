#!/usr/bin/env bash
# 真实 Zabbix 告警 → DevOpsAgent 工单 e2e。凭据只读环境变量，不落盘。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/scripts/e2e_real_zabbix.py" "$@"
