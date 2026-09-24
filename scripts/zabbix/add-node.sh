#!/usr/bin/env bash
# 一键把一台新服务器接入 Zabbix 监控（在母机/控制机上执行）。
#
# 用法：
#   bash scripts/zabbix/add-node.sh <新机IP> [SSH端口(默认22)] [SSH用户(默认root)]
#   未做免密时可用环境变量带上密码（需本机装有 sshpass）：
#     SSH_PASS='密码' bash scripts/zabbix/add-node.sh 1.2.3.4
#
# 流程：确认 SSH 可达（必要时自动 ssh-copy-id）→ 上传安装脚本并执行
#       → 轮询 Zabbix API 等待“自动注册”生效 → 输出结果。
# 可选：设置 DEVOPS_API_URL / DEVOPS_USER / DEVOPS_PASS 后，
#       注册成功会自动把机器登记进 devopsAgent 平台资产页（CMDB）。
#
# Zabbix API 连接信息优先读仓库根目录 .env 的 ZABBIX_URL/ZABBIX_USER/ZABBIX_PASSWORD。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTALLER="$ROOT/frontend/public/install-zabbix-agent.sh"

if [ $# -lt 1 ]; then
  echo "用法: bash $0 <新机IP> [SSH端口(默认22)] [SSH用户(默认root)]"
  exit 2
fi
NODE_IP="$1"; SSH_PORT="${2:-22}"; SSH_USER="${3:-root}"
SSH_TARGET="${SSH_USER}@${NODE_IP}"

# ---- 可选：devopsAgent 平台同步配置（读同目录 .env.devops，可被真实环境变量覆盖）----
ENV_FILE="$(cd "$(dirname "$0")" && pwd)/.env.devops"
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    case "$k" in
      DEVOPS_API_URL|DEVOPS_USER|DEVOPS_PASS)
        [ -n "$v" ] && [ -z "${!k:-}" ] && export "$k=$v" ;;
    esac
  done < "$ENV_FILE"
fi

# ---- Zabbix API 凭据（读 .env）----
if [ -f "$ROOT/.env" ]; then
  eval "$(grep -E '^ZABBIX_(URL|USER|PASSWORD)=' "$ROOT/.env" | sed 's/^/export /')"
fi
ZABBIX_URL="${ZABBIX_URL:-http://124.221.251.186:8081/api_jsonrpc.php}"
ZABBIX_USER="${ZABBIX_USER:-Admin}"
ZABBIX_PASSWORD="${ZABBIX_PASSWORD:-p@ssW0rd}"

api() { curl -fsS -m 15 -X POST "$ZABBIX_URL" -H 'Content-Type: application/json-rpc' -d "$1"; }
zbx_login() {
  TOKEN=$(api "{\"jsonrpc\":\"2.0\",\"method\":\"user.login\",\"params\":{\"user\":\"$ZABBIX_USER\",\"password\":\"$ZABBIX_PASSWORD\"},\"id\":1}" \
    | sed 's/.*"result":"\([^"]*\)".*/\1/')
  [ "${#TOKEN}" = "32" ]
}
host_count() {  # $1=主机名 → 返回数量
  api "{\"jsonrpc\":\"2.0\",\"method\":\"host.get\",\"params\":{\"countOutput\":true,\"filter\":{\"host\":[\"$1\"]}},\"auth\":\"$TOKEN\",\"id\":9}" \
    | sed 's/.*"result":"\([0-9]*\)".*/\1/'
}

echo "==> [1/4] 检查到 ${SSH_TARGET}:${SSH_PORT} 的 SSH 连通性"
if ! ssh -p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=6 "$SSH_TARGET" true 2>/dev/null; then
  if [ -n "${SSH_PASS:-}" ] && command -v sshpass >/dev/null 2>&1; then
    echo "    未免密 → 用 sshpass 自动 ssh-copy-id"
    SSHPASS=1 sshpass -p "$SSH_PASS" ssh-copy-id -p "$SSH_PORT" -o StrictHostKeyChecking=accept-new "$SSH_TARGET"
  else
    echo "!! SSH 未免密。请先执行一次: ssh-copy-id -p ${SSH_PORT} ${SSH_TARGET}"
    echo "   （或安装 sshpass 后用: SSH_PASS='密码' bash $0 ...）"
    exit 1
  fi
fi
ssh -p "$SSH_PORT" -o BatchMode=yes "$SSH_TARGET" "true" || { echo "!! SSH 仍不可达"; exit 1; }

echo "==> [2/4] 上传并执行安装脚本（新机装 zabbix-agent 并指向母机）"
scp -P "$SSH_PORT" -q "$INSTALLER" "$SSH_TARGET:/tmp/install-zabbix-agent.sh"
NODE_HOSTNAME=$(ssh -p "$SSH_PORT" "$SSH_TARGET" "bash /tmp/install-zabbix-agent.sh >/dev/null 2>&1; hostname") || {
  echo "!! 远程安装失败，登录新机手动执行: bash /tmp/install-zabbix-agent.sh"; exit 1
}
echo "    新机主机名: ${NODE_HOSTNAME}"

echo "==> [3/4] 等待 Zabbix 自动注册（最长约 3 分钟）"
zbx_login || { echo "!! Zabbix API 登录失败（检查 .env 的 ZABBIX_* 配置）"; exit 1; }
for i in $(seq 1 18); do
  sleep 10
  if [ "$(host_count "$NODE_HOSTNAME")" != "0" ]; then
    echo "==> [4/4] ✅ ${NODE_HOSTNAME} 已自动注册进 Zabbix（主机组 Linux servers + 主动式 Linux 模板）"
    echo "    查看: http://124.221.251.186:8081 → 数据采集 → 主机"
    # —— 可选第 5 步：同步登记到 devopsAgent 平台资产页（CMDB）——
    if [ -n "${DEVOPS_API_URL:-}" ] && [ -n "${DEVOPS_USER:-}" ] && [ -n "${DEVOPS_PASS:-}" ]; then
      PT=$(curl -fsS -m 10 -X POST "$DEVOPS_API_URL/api/v1/auth/login" -H 'Content-Type: application/json' \
        -d "{\"username\":\"$DEVOPS_USER\",\"password\":\"$DEVOPS_PASS\"}" \
        | sed 's/.*"access_token":"\([^"]*\)".*/\1/')
      if [ ${#PT} -gt 20 ]; then
        if curl -fsS -m 10 -X POST "$DEVOPS_API_URL/api/v1/assets/upsert" \
          -H "Authorization: Bearer $PT" -H 'Content-Type: application/json' \
          -d "{\"id\":\"$NODE_HOSTNAME\",\"hostname\":\"$NODE_HOSTNAME\",\"zabbix_host\":\"$NODE_HOSTNAME\",\"zabbix_server\":\"${ZBX_SERVER_IP:-124.221.251.186}\",\"source\":\"add-node\"}" >/dev/null; then
          echo "    已同步登记到 devopsAgent 资产页（CMDB）"
        else
          echo "    ! 平台资产登记失败（不影响 Zabbix 纳管），可稍后在资产页手工录入"
        fi
      else
        echo "    ! devopsAgent 登录失败，跳过资产登记（检查 DEVOPS_USER/DEVOPS_PASS）"
      fi
    else
      echo "    提示：设置 DEVOPS_API_URL/DEVOPS_USER/DEVOPS_PASS 后，可自动同步到 devopsAgent 资产页"
    fi
    exit 0
  fi
  echo "    等待注册... (${i}/18)"
done

echo "!! 3 分钟内未注册。排查清单："
echo "   1) 新机出站能否到母机 10051: nc -zv 124.221.251.186 10051"
echo "   2) 新机 agent 状态/日志: systemctl status zabbix-agent; tail -50 /var/log/zabbix/zabbix_agentd.log"
echo "   3) 确认配置: grep -E '^(Server|ServerActive|Hostname|HostMetadata)' /etc/zabbix/zabbix_agentd.conf"
exit 1
