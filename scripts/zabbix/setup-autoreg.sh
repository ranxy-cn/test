#!/usr/bin/env bash
# 一次性配置：在 Zabbix Server 上创建“自动注册”动作（幂等，可重复执行）。
# 效果：任何新机器装好 zabbix-agent 并带 HostMetadata=devops-auto 后，
#       自动加入主机监控（主机组 Linux servers + 主动式 Linux 模板）。
# 用法：bash scripts/zabbix/setup-autoreg.sh
# 可用环境变量覆盖：ZABBIX_URL / ZABBIX_USER / ZABBIX_PASSWORD / ZBX_GROUP / ZBX_TEMPLATE_HOST / ZBX_METADATA
set -euo pipefail

ZABBIX_URL="${ZABBIX_URL:-http://124.221.251.186:8081/api_jsonrpc.php}"
ZABBIX_USER="${ZABBIX_USER:-Admin}"
ZABBIX_PASSWORD="${ZABBIX_PASSWORD:-p@ssW0rd}"
ZBX_METADATA="${ZBX_METADATA:-devops-auto}"
ZBX_GROUP="${ZBX_GROUP:-Linux servers}"
ZBX_TEMPLATE_HOST="${ZBX_TEMPLATE_HOST:-Template OS Linux by Zabbix agent active}"

api() { curl -fsS -m 15 -X POST "$ZABBIX_URL" -H 'Content-Type: application/json-rpc' -d "$1"; }
jval() { printf '%s' "$1" | sed 's/.*"result":"'"\$2"'"\([^"]*\)".*/\1/'; }

# 1) 登录
TOKEN=$(api "{\"jsonrpc\":\"2.0\",\"method\":\"user.login\",\"params\":{\"user\":\"$ZABBIX_USER\",\"password\":\"$ZABBIX_PASSWORD\"},\"id\":1}" \
  | sed 's/.*"result":"\([^"]*\)".*/\1/')
[ ${#TOKEN} -eq 32 ] || { echo "!! 登录失败"; exit 1; }
echo "==> 登录成功"

# 2) 幂等检查：已有自动注册动作则跳过
EXIST=$(api "{\"jsonrpc\":\"2.0\",\"method\":\"action.get\",\"params\":{\"countOutput\":true,\"filter\":{\"eventsource\":2}},\"auth\":\"$TOKEN\",\"id\":2}" \
  | sed 's/.*"result":"\([0-9]*\)".*/\1/')
if [ "$EXIST" != "0" ]; then
  echo "==> 已存在自动注册动作（$EXIST 个），跳过创建。如需重建请先在 Zabbix 前端删除/停用。"
  exit 0
fi

# 3) 解析主机组 / 模板 ID
GROUPID=$(api "{\"jsonrpc\":\"2.0\",\"method\":\"hostgroup.get\",\"params\":{\"filter\":{\"name\":[\"$ZBX_GROUP\"]}},\"auth\":\"$TOKEN\",\"id\":3}" \
  | sed 's/.*"groupid":"\([0-9]*\)".*/\1/')
TEMPLATEID=$(api "{\"jsonrpc\":\"2.0\",\"method\":\"template.get\",\"params\":{\"filter\":{\"host\":[\"$ZBX_TEMPLATE_HOST\"]}},\"auth\":\"$TOKEN\",\"id\":4}" \
  | sed 's/.*"templateid":"\([0-9]*\)".*/\1/')
[ -n "$GROUPID" ] && [ -n "$TEMPLATEID" ] || { echo "!! 主机组或模板不存在：$ZBX_GROUP / $ZBX_TEMPLATE_HOST"; exit 1; }
echo "==> groupid=$GROUPID templateid=$TEMPLATEID"

# 4) 创建自动注册动作：条件 HostMetadata 包含 devops-auto；动作=加主机+入组+链模板
BODY=$(cat <<EOF
{"jsonrpc":"2.0","method":"action.create","params":{
  "name":"Auto registration: ${ZBX_METADATA}",
  "eventsource":2,
  "status":0,
  "esc_period":"1h",
  "filter":{"evaltype":0,"conditions":[{"conditiontype":24,"operator":2,"value":"${ZBX_METADATA}"}]},
  "operations":[
    {"operationtype":2},
    {"operationtype":4,"opgroup":[{"groupid":"${GROUPID}"}]},
    {"operationtype":6,"optemplate":[{"templateid":"${TEMPLATEID}"}]}
  ]},"auth":"$TOKEN","id":5}
EOF
)
RESULT=$(api "$BODY")
case "$RESULT" in
  *'"error"'*) echo "!! 创建失败：$RESULT"; exit 1 ;;
  *actionids*) echo "==> 自动注册动作创建成功：$RESULT" ;;
  *) echo "!! 未知响应：$RESULT"; exit 1 ;;
esac

# 5) 登出
api "{\"jsonrpc\":\"2.0\",\"method\":\"user.logout\",\"params\":[],\"auth\":\"$TOKEN\",\"id\":6}" >/dev/null
echo "==> 完成。之后新节点装好 agent（带 HostMetadata=${ZBX_METADATA}）即自动纳管。"
