#!/usr/bin/env bash
# 检测 ansible-runner / ansible-playbook。凭据只读环境变量，不打印私钥。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/scripts/ansible_ping.py" "$@"
