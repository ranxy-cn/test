# DevOpsAgent · 智能运维数字员工（二期骨架）

> **LLM 只做分析与建议 · 策略引擎做决定 · 执行器做动作 · 证据链做证明**

7×24 值守的运维数字员工：接收告警 → 编号任务单 → 采集证据 → AI 根因诊断 → 策略引擎决定自动修复 / 审批 / 升级 → 只跑固定 Playbook → 业务探测验证 → 审计与日报。

**一期安全红线不变。** `docker compose up` + `scripts/demo.sh` 在无真实凭据时走 mock，默认可跑。二期把 mock 闭环升级为「可切换真实集成」的适配层底座，并补齐资源锁、通知、备份骨架与冷却。

## 架构

```
接入层              编排层                            执行层
工作台 Vue       →  FastAPI 任务单状态机
Zabbix WH/API    →  LangGraph 排查 + Mock/可选 LLM    Celery Worker + Beat
通知 Webhook     →  纯代码策略引擎（绿/黄/红 + 冷却）    Playbook Runner（mock | 占位）
                 →  资源锁 / 审批 / 日报 / 审计         Vault 短凭证（mock | HTTP）
                 →  BackupJob 骨架                     独立业务探测验证器
```

数据面：PostgreSQL 16（任务单、证据 JSON、审计、资源锁、通知、备份）+ Redis（Celery）。知识库仍为关键词命中已审核手册（CPU / 磁盘与探针），预留 pgvector 扩展点。

数字员工岗位工号：**DE-OPS-001**（运维数字员工·小维），与执行器技术账号分离，审计中全程关联。

## 安全红线（代码强制）

- 大模型**不持有**生产管理员凭据；Vault 短凭证只在执行器内使用，进入 LLM 前会剥离 secret 类字段。
- 工具调用只接受 `asset_id + action_id + 结构化参数`；拒绝任意 shell、路径、跨租户目标。
- AI 输出经 Pydantic 校验的诊断书，`candidate_action_id` 必须来自 `playbooks/` 目录，禁止发明命令。
- 高风险动作（如 `ACT-DB-FAILOVER`）必须人工审批，绑定 **资产 + 作业版本 + 参数摘要**；参数变化或过期需重新审批。
- 验证失败或观察期复发：**停止、不循环重启**，带全量证据升级。
- 同资产同时只允许一个执行中处置（资源锁 + TTL/心跳）；同资产同动作失败后进入冷却，禁止自动再试。

## 快速开始

```bash
cp .env.example .env
docker compose up --build
```

- 工作台：http://localhost:8080
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health
- 集成状态：http://localhost:8000/api/v1/status

无 Docker 时（开发）：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL=sqlite:///./devops_agent.db USE_CELERY=false OBSERVATION_SECONDS=0 INTEGRATION_MODE=mock
cd backend && uvicorn app.main:app --reload --port 8000
# 另开终端
cd frontend && npm install && npm run dev
```

## 演示（绿灯 / 黄灯 / 红灯 + 锁冲突 + 备份）

```bash
chmod +x scripts/demo.sh
BASE=http://localhost:8000 ./scripts/demo.sh
```

也可在工作台点「模拟告警」，或用 curl：

```bash
# 绿灯：CPU 飙高 → ACT-ROLLING-RESTART → 自动执行 → 探测 3 次通过 → 已恢复
curl -s -X POST http://localhost:8000/api/v1/webhooks/zabbix \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: dev-webhook-secret' \
  -d '{"event_id":"e-green","asset_id":"ast-order-app-01","trigger_name":"CPU usage > 85% for 5 minutes","demo_scenario":"green"}'

# 黄灯：复制延迟 → ACT-DB-FAILOVER → 待审批
curl -s -X POST http://localhost:8000/api/v1/webhooks/zabbix \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: dev-webhook-secret' \
  -d '{"event_id":"e-yellow","asset_id":"ast-order-db-01","trigger_name":"MySQL replication lag too high","demo_scenario":"yellow"}'
# 批准
curl -s -X POST http://localhost:8000/api/v1/tickets/1/approve \
  -H 'Content-Type: application/json' \
  -d '{"approver":"王五","comment":"同意"}'

# 红灯：未知故障 → 不执行、升级人工
curl -s -X POST http://localhost:8000/api/v1/webhooks/zabbix \
  -H 'Content-Type: application/json' \
  -H 'X-Webhook-Secret: dev-webhook-secret' \
  -d '{"event_id":"e-red","asset_id":"ast-order-app-02","trigger_name":"mystery native crash","demo_scenario":"red"}'
```

幂等键 = `event_id|asset_id|job_version|action_type`。`ast-order-app-03` 处于维护窗口，告警只记录不处置。

演示模式观察期默认 **10 秒**（生产建议 10 分钟）。业务探测需连续 3 次通过。

## 任务单状态机

`待分析 → 待审批 → 待执行 → 执行中 → 验证中 → 已恢复 | 已升级`

策略引擎（纯代码，非模型）：

| 灯 | 条件 | 行为 |
| --- | --- | --- |
| 绿 | 低风险 + 白名单 + 可达 + 前置满足 + 不在冷却 | 自动执行 |
| 黄 | 高风险 / 未开放自动 | 挂起等待审批 |
| 红 | 未命中预案 / 前置失败 / 维护窗口 / 失败冷却 | 升级人工 |

白名单预案：

| ID | 风险 | 说明 |
| --- | --- | --- |
| `ACT-ROLLING-RESTART` | 低 | 摘流 → 重启 → 健康检查 |
| `ACT-DB-FAILOVER` | 高 | 主备切换，必须审批 |
| `ACT-CLEAN-TMPLOG` | 低 | 清理临时日志磁盘 |
| `ACT-RESTART-PROBE` | 低 | 重启旁路业务探针 |

滚动重启剧本见 `playbooks/ACT-ROLLING-RESTART.yaml`。同资产同动作失败后 `ACTION_FAIL_COOLDOWN_SECONDS`（默认 1800）内禁止自动再试。

## 二期：如何从 mock 切到真实 Zabbix / Ansible / Vault

业务代码只依赖 `integrations/` 协议（Adapter），由工厂按配置注入。**不要把 mock 写进 pipeline。**

统一开关：

```bash
INTEGRATION_MODE=mock   # 默认。无凭据、可演示
INTEGRATION_MODE=real   # 尝试真实客户端；缺凭据的分项自动回退 mock
```

分项覆盖（留空则跟随全局）：

```bash
ZABBIX_MODE=real
ANSIBLE_MODE=mock
VAULT_MODE=real
```

### 连接真实 Zabbix（只读）

排查链路的指标 / 事件 / 主机解析走 `integrations/zabbix/` 适配层，**不写监控配置、不应答告警、不执行远程脚本**。JSON-RPC 方法名有白名单，`script.execute` / `configuration.*` / `event.acknowledge` 等会直接拒绝。

**权限**：API Token（或只读用户）只需能调用 `host.get`、`item.get`、`history.get`/`trend.get`、`event.get`、`problem.get`、`trigger.get`、`apiinfo.version`。不要授予 Admin / 可执行脚本的角色。

```bash
ZABBIX_URL=https://zabbix.example.com/api_jsonrpc.php
ZABBIX_TOKEN=xxxxxxxx          # 优先 API token；或改用 ZABBIX_USER / ZABBIX_PASSWORD
ZABBIX_MODE=auto               # mock | real | auto（推荐 auto）
ZABBIX_VERIFY_SSL=true
ZABBIX_TIMEOUT_SECONDS=8
ZABBIX_RETRIES=2
```

- `mock`（默认）：内存模拟，demo 不需要实例。
- `real`：有 URL+凭据则注入 HTTP 客户端；调用失败时该次采证降级 mock 并在证据里标注「未映射，使用 mock/降级」。
- `auto`：有 URL+Token（或 user/password）**且** `apiinfo.version` + 轻量 `host.get` 通过才用 real，否则 mock。

**认证差异**：

| 版本 | 方式 | 说明 |
| --- | --- | --- |
| Zabbix **5.0** | `user.login` + JSON-RPC `auth` | 参数名为 `user` + `password`，session id 放请求体 `auth` 字段；**没有** API Token / Bearer |
| Zabbix **6.x / 7.x** | API Token（优先）或 `user.login` | Token 可用 `Authorization: Bearer`；登录参数名为 `username` |

生产环境请**立刻修改默认 Admin 密码**，并改用只读用户。密码只放环境变量，不要写入仓库或 `.env` 提交。

#### 对接联调 Zabbix（5.0.41）

```bash
export ZABBIX_URL=http://124.221.251.186:8081/api_jsonrpc.php
export ZABBIX_USER=Admin
export ZABBIX_PASSWORD=***          # 不要写入仓库
export ZABBIX_MODE=auto             # 或 real
python3 scripts/zabbix_ping.py
# 工作台：http://localhost:8000/api/v1/status  应看到 zabbix.mode=real、version=5.0.41
```

工作台 **集成状态** 展示：请求模式、生效模式、版本、延迟、最近错误。

主机映射：CMDB `external_id`（Zabbix hostid）/ `zabbix_host` / `hostname` 对齐 webhook 的 `hostid`/`host`。种子数据含默认主机 `Zabbix server`（hostid `10084` → `ast-zabbix-server`）。对不上时证据 `mapped=false` 并注明降级。

Webhook 同时接受演示字段与 **Zabbix 5.0 媒体类型**常见宏（点号键亦可）：`{EVENT.ID}` `{HOST.NAME}` `{HOST.HOST}` `{HOST.ID}` `{TRIGGER.NAME}` `{EVENT.SEVERITY}` `{EVENT.NSEVERITY}` `{EVENT.NAME}` `{EVENT.VALUE}`。

媒体类型模板可用宏；运行时展开后的真实形态见 `scripts/zabbix_webhook.example.json`。仍可继续用 `asset_id` + `event_id` + `trigger_name` 跑 demo。

### 真实告警 e2e

与 `scripts/demo.sh`（纯 mock 三色路径，不访问 Zabbix）不同：本脚本用 **真实 Zabbix 5.0** 只读 API 取当前问题（若无打开的 problem，则注入一条 5.0 webhook 宏兼容载荷），由 DevOpsAgent **立案 → 调查/诊断 → 策略引擎 → 自动修复或待审批/升级**，证据须带 real API 的 host/items/problems。

```bash
export ZABBIX_URL=http://124.221.251.186:8081/api_jsonrpc.php
export ZABBIX_USER=Admin
export ZABBIX_PASSWORD='***'    # 本地填写，禁止写入仓库 / .env 提交
export ZABBIX_MODE=real
docker compose up --build -d
./scripts/e2e_real_zabbix.sh
```

**期望终态**（任一即可，超时非 0）：

| 终态 | 含义 |
| --- | --- |
| `recovered` | 绿灯，已自动执行白名单 Playbook 并探测通过 |
| `pending_approval`（脚本打印为 `waiting_approval`） | 黄灯，等待人工审批 |
| `escalated` | 红灯或验证失败，已升级且不循环重启 |

脚本打印 ticket id、状态流转、证据是否来自 real Zabbix、策略结果。成功退出码 0。

**缺凭据**：未设置 `ZABBIX_URL` 或 `ZABBIX_MODE=real` 时脚本清晰失败（请改用 mock demo）。有 URL 但没有密码时，会对公开 `apiinfo.version` 做探测并注入 webhook，输出标明「未配置密码，采证未走 real login」。

请把同一组 `ZABBIX_*` 传给 `docker compose` 的 api/worker，否则立案成功但采证仍会降级 mock。

有实例时自测（不会写 Zabbix；5.0 用用户会话，6.x 可用 Token）：

```bash
export ZABBIX_URL=... ZABBIX_USER=Admin ZABBIX_PASSWORD=***
# 或：export ZABBIX_TOKEN=...
python3 scripts/zabbix_ping.py
```

排错：Token 无效 → 生效仍可能回退 mock；TLS 证书问题设 `ZABBIX_VERIFY_SSL=false`（仅实验）；方法被拒说明代码白名单生效，不要试图开写权限。

### Ansible（Playbook Runner）

```bash
ANSIBLE_RUNNER_ENABLED=true
ANSIBLE_MODE=real
ANSIBLE_PRIVATE_DATA_DIR=/tmp/ansible-runner
# 镜像内需 pip install ansible-runner；未安装则回退 mock
```

真实路径当前是 **ansible-runner / SSH 占位**：仍只跑白名单 YAML，拒绝自由命令。未接完整 inventory，不会在未配置时对主机下手。

### Vault（短期凭据）

```bash
VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=s.xxxxx
VAULT_MODE=real
```

短凭证只在 Runner 调用栈内领取/吊销，审计只记 `lease_id`。Mock 实现同样带 TTL，密钥不进入 LLM。

### 通知

工作台「通知」页始终写入收件箱。可选外发：

```bash
NOTIFY_WEBHOOK_URL=https://hooks.example.com/devops
```

事件：任务立案、待审批、已恢复、已升级（资源锁排队也会记一笔）。

切换后打开工作台 **集成状态** 页查看「请求模式 / 生效模式 / 健康探测」。无真实密钥时 demo 必须仍为 mock。

## 资源锁与备份骨架

- **资源锁**：同一 `asset_id` 同时只允许一个执行中处置；冲突任务保持 `pending_execution` 并写审计/通知。锁带 TTL 与心跳，过期可被下一任务抢占，进程重启后排队任务由 Celery beat（或 `POST /api/v1/tickets/{id}/retry-execution`）恢复。
- **备份骨架**：`BackupJob` / `BackupRun`。可 `POST /api/v1/backups/{id}/run` 触发一次 mock 备份；**备份成功**与**恢复验证**分开展示。日报不再写「未检查」，而使用真实字段（仍可为 mock 数据）。不是完整灾备产品。

## 测试

```bash
cd backend && pytest -q
```

覆盖：Webhook 鉴权与去重、状态机、策略引擎、诊断 schema、拒绝任意命令、绿灯/黄灯/红灯闭环、适配工厂切换、资源锁互斥与过期抢占、通知触发、备份状态字段、失败冷却禁止循环。

## 一期 Mock vs 二期真实接入

| 模块 | 一期 | 二期（本仓库） |
| --- | --- | --- |
| 监控 | Mock Zabbix Webhook | Webhook 宏对齐 + 只读 JSON-RPC（host/item/history/problem）；auto 探测失败回退 mock |
| 排查工具 | 并行 Mock 指标/日志/发版/依赖 | 指标走 ZabbixClient 工厂 |
| RAG | 关键词命中手册 | 仍为关键词；预留 pgvector |
| LLM | 无 Key 时确定性 Mock | 不变 |
| 执行 | Mock Ansible Runner | PlaybookRunner 工厂；真实为 ansible-runner/SSH 占位 |
| 密钥 | Mock Vault 30 分钟租约 | VaultClient 工厂；可选 HTTP |
| 备份 | 日报「未检查」 | BackupJob/Run 骨架，成功 ≠ 恢复验证 |
| 锁 / 通知 | 无 | 资源锁 + Notifier（日志/Webhook/工作台） |
| 身份 | 单租户种子数据 | 不变 |

## 目录

```
backend/app/integrations/   Zabbix / Ansible / Vault 适配与工厂
backend/app/services/      流水线、锁、通知、冷却、备份
backend/app/routers/ops.py  状态 / 锁 / 通知 / 备份 API
playbooks/                 版本化预案白名单（含清理日志、重启探针）
knowledge/                 已审核操作手册
frontend/                  Vue 3 + Element Plus（任务 / 通知 / 备份 / 集成状态）
scripts/demo.sh            三色路径 + 锁冲突 + mock 备份
scripts/zabbix_ping.py     有实例时只读连通性自测
scripts/e2e_real_zabbix.sh 真实 Zabbix 告警 → 工单终态（凭据走环境变量）
scripts/zabbix_webhook.example.json  Zabbix 5.0 媒体类型真实形态 payload
```

## 仍未做的范围

- 不要求提供真实生产密钥才能跑通 demo（缺凭据一律 mock）。
- 不接 Kubernetes、不训练模型、不做完整 restic/灾备编排。
- 真实 Ansible inventory / SSH 批量执行、HashiCorp Vault 动态库完整策略、企业 IAM、pgvector RAG：均未做。
- 不实现 Zabbix 自动应答、远程命令或改监控配置（只读白名单强制）。
- 备份仅为任务/运行记录骨架，**备份成功 ≠ 可恢复**，隔离恢复也只是 mock 标记。

本仓库原先为 ERC4907 沙箱示例，已替换为 DevOpsAgent 工程。
