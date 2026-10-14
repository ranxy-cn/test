#!/usr/bin/env bash
# 部署新知AI-智能巡检到 103.236.88.52（NAT VPS）
# 流程：本地打包(git archive HEAD + agent 二进制) → scp 上传 → 远端解包/构建/up -d
#      （nohup 后台防 SSH 掉线）→ 轮询结果 → 验证
set -euo pipefail

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$ROOT"

REMOTE_DIR=/opt/devops-platform
SSH=".agent/ssh-run-103.exp"
SCP=".agent/scp-send-103.exp"
BIN="backend/agent/dist/agent-linux-amd64"

log() { echo "[deploy-103] $*"; }

# ---------- 0. 预检 ----------
[ -x "$SSH" ] || { echo "缺辅助脚本 $SSH"; exit 1; }
[ -x "$SCP" ] || { echo "缺辅助脚本 $SCP"; exit 1; }

if [ ! -f "$BIN" ]; then
  log "agent 二进制缺失，自动构建（make -C backend/agent build-go）"
  make -C backend/agent build-go
fi
[ -f "$BIN" ] || { echo "构建后仍缺 $BIN，请检查 backend/agent/Makefile"; exit 1; }

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  log "警告：工作区有未提交修改，git archive 只打包 HEAD；如需带上请先 commit"
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# ---------- 1. 打包：git 跟踪文件 + agent 二进制 ----------
log "打包（git archive HEAD + ${BIN}）"
git archive --format=tar -o "$STAGE/base.tar" HEAD
mkdir -p "$STAGE/backend/agent/dist"
cp "$BIN" "$STAGE/backend/agent/dist/"
tar -rf "$STAGE/base.tar" -C "$STAGE" backend/agent/dist/agent-linux-amd64
gzip -kf "$STAGE/base.tar"
log "包大小：$(du -h "$STAGE/base.tar.gz" | cut -f1 | tr -d ' ')"

# 远端执行脚本（单独上传，避免复杂引号）
cat > "$STAGE/remote.sh" <<'EOF'
#!/bin/sh
cd /opt/devops-platform || { echo 90 > /tmp/deploy-103.done; exit 0; }
echo 99 > /tmp/deploy-103.done
cp .env .env.pre-deploy 2>/dev/null
tar -xzf /tmp/deploy-103.tar.gz || { echo 91 > /tmp/deploy-103.done; exit 0; }
export COMPOSE_PROFILES=bundled-db
docker compose build || { echo 92 > /tmp/deploy-103.done; exit 0; }
docker compose up -d || { echo 93 > /tmp/deploy-103.done; exit 0; }
echo 0 > /tmp/deploy-103.done
EOF

# ---------- 2. 上传 ----------
log "上传代码包与远端脚本"
"$SCP" "$STAGE/base.tar.gz" /tmp/deploy-103.tar.gz >/dev/null
"$SCP" "$STAGE/remote.sh" /tmp/deploy-103-remote.sh >/dev/null

# ---------- 3. 启动远端部署（nohup 后台，防 SSH 掉线） ----------
"$SSH" "rm -f /tmp/deploy-103.done /tmp/deploy-103-run.log; nohup sh /tmp/deploy-103-remote.sh > /tmp/deploy-103-run.log 2>&1 & echo LAUNCHED" | tail -1

# ---------- 4. 轮询构建结果（最长 12 分钟） ----------
log "等待远端构建完成（每 15 秒轮询，最长 12 分钟）..."
code=""
for i in $(seq 1 48); do
  sleep 15
  code=$("$SSH" "cat /tmp/deploy-103.done 2>/dev/null" | tr -d '\r' | tail -1)
  case "$code" in
    0|90|91|92|93) break ;;
  esac
done
if [ "$code" != "0" ]; then
  echo "[deploy-103] 部署失败（exit=${code}；91=解包 92=build 93=up，空=超时），远端日志："
  "$SSH" "tail -40 /tmp/deploy-103-run.log" || true
  exit 1
fi

# ---------- 5. 验证 ----------
log "验证容器 / agent 二进制 / 健康检查"
"$SSH" "cd $REMOTE_DIR && COMPOSE_PROFILES=bundled-db docker compose ps --format '{{.Name}} {{.Status}}' && docker exec devops-platform-api-1 ls -l /app/agent/dist/agent-linux-amd64 && curl -sS -m 5 http://127.0.0.1:8000/health"

health=$(curl -sS -m 8 http://103.236.88.52:33608/health)
front=$(curl -sS -o /dev/null -m 8 -w '%{http_code}' http://103.236.88.52:60658/)
echo "外部 API health: $health"
echo "外部 frontend http_code: $front"
case "$health" in
  *'"ok":true'*) ;;
  *) echo "[deploy-103] API 健康检查异常"; exit 1 ;;
esac
[ "$front" = "200" ] || { echo "[deploy-103] 前端非 200"; exit 1; }

log "部署完成并验证通过"
