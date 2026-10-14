#!/usr/bin/env bash
# 新知AI-智能巡检 一键部署到 186：同步代码（不走 git）-> SKIP_PULL=1 部署 -> 验证
# 详细说明见同目录上级 SKILL.md；服务器信息见项目 .agent/server-access.md
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
KEY="$ROOT/.agent/ssh_key"
HOST_TARGET="ranxiaoying@124.221.251.186"
DEST="/home/ranxiaoying/devops-agent"
SSH_CMD="ssh -i $KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"

if [ ! -f "$KEY" ]; then
  echo "ERROR: 找不到私钥 ${KEY}，请先阅读 .agent/server-access.md" >&2
  exit 2
fi

echo "==> [1/3] 同步代码到 ${HOST_TARGET}:${DEST} （排除 .git/.agent/.env/._* 等）"
COPYFILE_DISABLE=1 tar czf - --no-xattrs \
  --exclude '.git' --exclude '.agent' --exclude '.trae' \
  --exclude 'node_modules' --exclude '.venv' --exclude 'venv' \
  --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '.DS_Store' --exclude '._*' \
  --exclude 'frontend/dist' --exclude './.env' \
  --exclude '*.tgz' --exclude 'deploy_work' \
  -C "$ROOT" . | $SSH_CMD "$HOST_TARGET" "cd $DEST && tar xzf -"

echo "==> [2/3] 远程执行部署（SKIP_PULL=1）"
$SSH_CMD "$HOST_TARGET" "cd $DEST && SKIP_PULL=1 bash scripts/deploy.sh" | tail -15

echo "==> [3/3] 验证"
curl -sf -m 15 http://124.221.251.186:8000/health \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('API health ok:', d['ok'])"
FE_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 http://124.221.251.186:8080/)
echo "前端 HTTP 状态: $FE_CODE"
[ "$FE_CODE" = "200" ] || { echo "前端验证失败" >&2; exit 1; }
echo "==> 部署完成"
