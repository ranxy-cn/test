---
name: deploy-186
description: Deploy 新知AI-智能巡检 to the 186 prod server by reading .agent access info first, syncing files without git, running SKIP_PULL=1 deploy, then verifying health. Use when user says 上线部署, 部署到186, deploy, release. Not for local dev servers.
---

# 部署新知AI-智能巡检到 186 生产服务器

把当前项目部署到生产服务器 124.221.251.186（目录 /home/ranxiaoying/devops-agent）。
**不走 git**（服务器目录不是 git 仓库，用户也明确要求不提交 git），直接同步文件。

## 第零步：部署前先 git commit（必须，用户已授权的固定流程）

每次部署前必须先把本地改动提交：

- `git add` 相关文件 + `git commit`（信息用中文概括「改了什么 + 为什么」）；
- **只 commit 不 push**（除非用户明确要求 push）；
- 服务器同步走 tar 管道、与 git 无关，但本地提交是回滚与追溯的依据，不许跳过。

## 第一步：先读 .agent（必须，每次都要）

任何操作之前，先读以下文件：

1. `.agent/server-access.md` — 服务器 IP、账号、密码、Zabbix 栈、容器布局
2. `.agent/server.env` — 服务器上 `/home/ranxiaoying/devops-agent/.env` 的内容基线（严禁被覆盖）
3. `.agent/ssh_key` — 免密 SSH 私钥，已装入服务器 `ranxiaoying` 用户

若 `.agent/ssh_key` 连接失败（权限被拒），回退用 `.agent/ssh-run.exp "<命令>"`（expect 脚本，
密码自动从 server-access.md 解析）。

## 第二步：同步 + 部署 + 验证（一条命令）

```bash
bash .trae/skills/deploy-186/scripts/deploy.sh
```

脚本做三件事：tar 管道同步代码 → 服务器执行 `SKIP_PULL=1 bash scripts/deploy.sh`（docker-compose
重建并健康检查）→ 本地 curl 验证。构建正常约 1-3 分钟，前端 npm build 是主要耗时。

## 第三步：验证通过标准

- `curl http://124.221.251.186:8000/health` 返回 `"ok":true` 且 zabbix/ansible 均 `ready_for_real:true`
- `curl -o /dev/null -w '%{http_code}' http://124.221.251.186:8080/` 返回 200
- 登录验证（可选）：`POST /api/v1/auth/login`，body `{"username":"admin","password":"Adm1n@Dev0ps#2026"}`，能拿到 `access_token`

## 故障排查

- **api 容器 Restarting / 健康检查超时**：先看日志
  `ssh ... "cd /home/ranxiaoying/devops-agent && docker-compose logs --tail=40 api"`
  （服务器是 docker-compose v1，命令带连字符）。
- **报错 `SyntaxError: source code string cannot contain null bytes`**：macOS 同步把 `._*`
  AppleDouble 垃圾文件带上了服务器，alembic 把它们当 Python 模块加载导致崩溃。修复：
  `ssh ... "find /home/ranxiaoying/devops-agent -name '._*' -type f -delete"` 然后重新部署。
  部署脚本已用 `COPYFILE_DISABLE=1` 和 `--exclude '._*'` 预防。
- **本地 shell 检测文件是否含 null 字节**：不要用 `grep $'\x00'`（zsh 会把 NUL 变成空模式，
  匹配所有文件造成全量误报）。用 `python3 -c "import pathlib;[print(p) for p in pathlib.Path('backend').rglob('*.py') if b'\x00' in p.read_bytes()]"`。
- **服务器内存仅约 1.7G 可用**：不要并行起多个构建任务。

## 铁律

- 同步时**永远排除** `./.env`（服务器生产配置，本地没有），也不要用任何带 `--delete` 的同步方式。
- 部署脚本必须带 `SKIP_PULL=1`。
- 服务器上其他容器（zabbix 系列、hbbs/hbbr、openvpn）与本项目无关，禁止触碰。
