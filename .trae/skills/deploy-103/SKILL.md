---
name: deploy-103
description: Deploy 新知AI-智能巡检 to the 103 NAT VPS (103.236.88.52, /opt/devops-platform). Use when user says 部署103, 重新部署, 上线103, deploy 103, or wants finished code changes pushed to 103. Not for 186 or local dev.
---

# 部署新知AI-智能巡检到 103 服务器

把项目部署到 103.236.88.52（NAT VPS，部署目录 /opt/devops-platform，docker compose 栈）。
每次代码改动完成、用户要求「重新部署 103」时执行本 skill。

## 执行（一条命令）

```bash
bash .trae/skills/deploy-103/scripts/deploy-103.sh
```

脚本自动完成：打包（git archive HEAD + agent 二进制）→ scp 上传 → 远端解包 →
`docker compose build && up -d`（nohup 后台 + 轮询，防 SSH 掉线）→ 验证。
构建约 3-8 分钟（前端 npm build 最耗时），脚本会一直等到验证通过才退出。

## 前置条件（脚本自动检查）

1. `.agent/ssh-run-103.exp`、`.agent/scp-send-103.exp` — 免密 expect 辅助脚本
   （密码运行时从 `.agent/server-access.md` 解析，不要把密码写进命令行或日志）
2. `backend/agent/dist/agent-linux-amd64` — Go agent 二进制；缺失时脚本自动执行
   `make -C backend/agent build-go` 构建

## 关键事实（勿重新探索）

- SSH：`root@103.236.88.52 -p 44006`；SSH 空闲易断 → 长命令必须 nohup 后台 + 轮询（脚本已内置）
- NAT 端口：外部 33608 → API 8000；外部 60658 → 前端（宿主 8080 → 容器 80）
- 平台自访问地址：`PLATFORM_PUBLIC_URL=http://103.236.88.52:33608`（.env 内，agent 回报用）
- compose 栈：api/worker/beat/frontend + bundled mysql/redis，需 `COMPOSE_PROFILES=bundled-db`，
  docker compose v2（命令带空格，服务器 Docker 26.1.3）
- `/opt/devops-platform/.env` 是生产配置，**严禁覆盖/删除**（git archive 不含它，天然安全）
- mysql 数据在命名卷，重建容器不丢数据
- Docker Hub 拉镜像不通：基础镜像已在服务器本机，构建不要加 `--pull`
- api 容器启动时自动执行 `alembic upgrade head`，无需手动迁移

## 打包铁律（踩过的坑）

- 用 `git archive HEAD` 打包：只含跟踪文件，天然排除 `._*` AppleDouble（会毒化 alembic 导致
  api 崩溃）、.git、.agent（明文服务器密码！）、.idea、.tmp 等
- **必须单独追加** `backend/agent/dist/agent-linux-amd64`——`.gitignore` 的 `dist/` 规则会把它
  排除，漏掉会导致添加母机/子机时报「agent 文件缺失：dist/agent-linux-amd64」
- git archive 只打包已提交内容，工作区未提交改动不会进包（脚本会警告）

## 验证通过标准

- 远端 `curl 127.0.0.1:8000/health` 返回 `"ok":true`
- 本地 `curl http://103.236.88.52:33608/health` 正常
- 本地 `curl -o /dev/null -w '%{http_code}' http://103.236.88.52:60658/` 返回 200
- api 容器内存在 `/app/agent/dist/agent-linux-amd64`
- `docker compose ps` 六个容器全部 Up/healthy

## 故障排查

- 部署失败：脚本会自动输出远端日志尾部 `/tmp/deploy-103-run.log`
  （exit code 含义：91=解包失败 92=build 失败 93=up 失败）
- api 起不来：`.agent/ssh-run-103.exp "cd /opt/devops-platform && docker compose logs --tail=40 api"`
- 报 null bytes 语法错误：服务器上 `find /opt/devops-platform -name '._*' -type f -delete` 后重跑
- 服务器 2核4G：不要并行多个构建任务
- 服务器上其他业务（nonebot 等）与本项目无关，禁止触碰
