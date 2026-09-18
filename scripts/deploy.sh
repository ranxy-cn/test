#!/usr/bin/env bash
# 一键 Docker 部署：拉取最新代码 → docker compose 构建启动 → API 健康检查
# 用法：
#   bash scripts/deploy.sh                 # 在项目根目录部署（git pull + 构建）
#   SKIP_PULL=1 bash scripts/deploy.sh     # 跳过 git pull，仅重新构建启动
set -euo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"

cd "$PROJECT_DIR"

if [ -d .git ] && [ "${SKIP_PULL:-0}" != "1" ]; then
  echo "==> 拉取最新代码"
  git pull --ff-only
fi

echo "==> docker compose 构建并启动"
docker compose -f "$PROJECT_DIR/docker-compose.yml" up --build -d

echo "==> 等待 API 健康检查（最多 60 秒）"
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "==> 部署完成，API 已就绪：$HEALTH_URL"
    docker compose -f "$PROJECT_DIR/docker-compose.yml" ps
    exit 0
  fi
  sleep 2
done

echo "!! 健康检查超时，请查看日志：docker compose -f $PROJECT_DIR/docker-compose.yml logs -f api" >&2
docker compose -f "$PROJECT_DIR/docker-compose.yml" ps >&2
exit 1
