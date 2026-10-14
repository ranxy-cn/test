# 运维与基础设施（playbooks / knowledge / scripts / compose / CI / env）

## playbooks/（预案白名单，安全红线的载体）

- **catalog.yaml**：预案目录索引（`policy_version: policy-v1.0.0` + `playbooks: [{id, file}]`）。AI 只能引用这里的 id，不能发明命令。现有 4 个：ACT-ROLLING-RESTART / ACT-DB-FAILOVER / ACT-CLEAN-TMPLOG / ACT-RESTART-PROBE
- **顶层 *.yaml**（如 ACT-CLEAN-TMPLOG.yaml）：预案元数据声明，供策略引擎/AI 消费。字段：id/name/version/description、`risk`（low/high 决定绿/黄灯）、`auto_allowed`（是否允许自动执行）、`requires_db_ok/requires_reachable`（前置条件）、`restart_cooldown`、`success_criteria/stop_conditions`、`allowed_params`（参数 schema+默认值）、`steps[]`（含 mock_seconds 模拟耗时）
- **ansible/ 子目录 *.yml**：同 id 的真实 Ansible playbook（ansible-runner 执行，仅白名单可跑）
- 关系：顶层 yaml = 声明（元数据/风险/参数）；ansible/*.yml = 实现（真实执行体）。新增预案两边都要加

## knowledge/

内置 runbook，参与 RAG 检索与 AI 分析（与上传文档同源）：
- **cpu_spike_runbook.md**（RB-CPU-001）：CPU 飙高排查；Full GC/发版卡死→ACT-ROLLING-RESTART，数据库故障→ACT-DB-FAILOVER（需审批），未命中→升级人工
- **disk_tmp_runbook.md**（RB-DISK-001）：临时日志堆积→ACT-CLEAN-TMPLOG；探针挂死→ACT-RESTART-PROBE

## scripts/

- **demo.sh**：三色路径端到端演示（绿→recovered、黄→pending_approval 后人工 approve、红→escalated 不执行）+ 锁冲突 + mock 备份
- **deploy.sh**：一键 Docker 部署（compose v2/v1 兼容；`SKIP_PULL=1` 跳过 git pull；`docker compose up --build -d` + 健康检查 60s）
- **ansible_ping.py / .sh**：检测 Ansible 真实链路可用性（requested/effective 模式、fallback 原因、runner 可导入性、inventory/SSH key、白名单 play 列表）

## docker-compose.yml

- 服务：api（先 `alembic upgrade head` 再 uvicorn:8000）、worker（celery worker concurrency=2）、beat、frontend（8080→80）、redis（7-alpine）；mysql 为可选 profile `bundled-db`（默认不启动，生产用宿主机 MySQL）
- **只读挂载**（api/worker/beat 共用）：`./playbooks`、`./knowledge`、`./inventory`；api/worker 另挂 `./keys`（巡检私钥）与宿主机 `/proc:/host/proc:ro`（实时监控读宿主机网口计数器）
- redis/mysql 不映射宿主机端口；后端三服务共享 `x-backend-env` 锚点环境变量

## .github/workflows/ci-cd.yml

触发 push main / PR / 手动。三个 job：
1. backend-tests：Python 3.11 `pytest -q`
2. frontend-build：Node 22 `npm ci && npm run build`
3. deploy（仅 main/手动，needs 前两者）：tar 打包（排除 .git/.venv/node_modules/dist/.agent/.env）→ scp → ssh 解压执行 `bash scripts/deploy.sh .`；secrets：DEPLOY_HOST/USER/SSH_KEY/PORT/PATH

## .env.example 环境变量分组

- 存储：DATABASE_URL（MySQL，特殊字符需 URL 编码）、REDIS_URL、USE_CELERY、DEMO_MODE
- 鉴权：JWT_SECRET（生产必须）、ACCESS_TOKEN_EXPIRE_MINUTES（720）、ADMIN_INITIAL_PASSWORD；SECRET_ENCRYPT_KEY（敏感凭据落库加密专用密钥，多环境共库时必须一致，留空回退 JWT_SECRET）
- 执行节奏：OBSERVATION_SECONDS（演示 10 / 生产建议 600）、PROBE_REQUIRED_PASSES、PROBE_INTERVAL_SECONDS、RESTART_COOLDOWN_SECONDS、ACTION_FAIL_COOLDOWN_SECONDS、LOCK_TTL_SECONDS
- 集成开关：INTEGRATION_MODE（mock|real|分项 auto）、ANSIBLE_MODE/VAULT_MODE、ANSIBLE_RUNNER_ENABLED、ANSIBLE_CHECK_MODE、ANSIBLE_TIMEOUT_SECONDS 等
- LLM（OpenAI 兼容可选）：OPENAI_API_KEY/BASE_URL/MODEL；compose 另有 CHAT_* 用于对话
- Webhook：WEBHOOK_SECRET、WEBHOOK_AUTO_TICKET（compose 内，默认 false）
- 其他：PLATFORM_PUBLIC_URL（子机 agent 上报地址拼接）、NOTIFY_WEBHOOK_URL、NETDATA_*、DIAG_SSH_PASSWORD、STRESS_TOOLS_ENABLED/STRESS_MAX_SECONDS、KNOWLEDGE_MAX_UPLOAD_BYTES、POLICY_VERSION、TENANT_ID、EMPLOYEE_ID

## 相关既有 skill

- `deploy-186`：部署到 186 生产服务器（不走 git，先读 .agent/ 访问信息）。部署任务不要用本 skill
