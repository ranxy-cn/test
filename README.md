# DevOpsAgent · 智能运维数字员工（一期 MVP）

> **LLM 只做分析与建议 · 策略引擎做决定 · 执行器做动作 · 证据链做证明**

7×24 值守的运维数字员工：接收告警 → 编号任务单 → 采集证据 → AI 根因诊断 → 策略引擎决定自动修复 / 审批 / 升级 → 只跑固定 Playbook → 业务探测验证 → 审计与日报。

本仓库是可运行的一期演示，**不连接真实 Zabbix / Ansible / Vault / LLM**。`docker compose up` 即可走通「CPU 飙高 → 滚动重启」绿灯路径，以及黄灯审批、红灯升级。

## 架构

```
接入层        编排层                         执行层
工作台 Vue  →  FastAPI 任务单状态机
Zabbix WH  →  LangGraph 排查 + Mock LLM     Celery Worker
           →  纯代码策略引擎（绿/黄/红）      Mock Ansible Runner
           →  审批 / 日报 / 审计             Mock Vault 短凭证
                                            独立业务探测验证器
```

数据面：PostgreSQL 16（任务单、证据 JSON、审计）+ Redis（Celery）。知识库一期用关键词命中已审核手册《CPU 飙高排查手册》，预留 pgvector 扩展点。

数字员工岗位工号：**DE-OPS-001**（运维数字员工·小维），与执行器技术账号分离，审计中全程关联。

## 安全红线（代码强制）

- 大模型**不持有**生产管理员凭据；Vault 短凭证只在执行器内使用，进入 LLM 前会剥离 secret 类字段。
- 工具调用只接受 `asset_id + action_id + 结构化参数`；拒绝任意 shell、路径、跨租户目标。
- AI 输出经 Pydantic 校验的诊断书，`candidate_action_id` 必须来自 `playbooks/` 目录，禁止发明命令。
- 高风险动作（如 `ACT-DB-FAILOVER`）必须人工审批，绑定 **资产 + 作业版本 + 参数摘要**；参数变化或过期需重新审批。
- 验证失败或观察期复发：**停止、不循环重启**，带全量证据升级。

## 快速开始

```bash
cp .env.example .env
docker compose up --build
```

- 工作台：http://localhost:8080
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

无 Docker 时（开发）：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
export DATABASE_URL=sqlite:///./devops_agent.db USE_CELERY=false OBSERVATION_SECONDS=0
cd backend && uvicorn app.main:app --reload --port 8000
# 另开终端
cd frontend && npm install && npm run dev
```

## 演示（绿灯 / 黄灯 / 红灯）

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
| 绿 | 低风险 + 白名单 + 可达 + DB 正常 + 30 分钟内未重启 | 自动执行 |
| 黄 | 高风险 / 未开放自动 | 挂起等待审批 |
| 红 | 未命中预案 / 前置失败 / 维护窗口 | 升级人工 |

滚动重启剧本：`摘流 → 重启 → 健康检查`，见 `playbooks/ACT-ROLLING-RESTART.yaml`。

## 测试

```bash
cd backend && pytest -q
```

覆盖：Webhook 鉴权与去重、状态机、策略引擎、诊断 schema、拒绝任意命令、绿灯/黄灯/红灯闭环。

## 一期 Mock vs 二期真实接入

| 模块 | 一期 | 二期扩展点 |
| --- | --- | --- |
| 监控 | Mock Zabbix Webhook | 真实 Zabbix 7 Webhook 字段映射 |
| 排查工具 | 并行 Mock 指标/日志/发版/依赖 | 只读监控与日志 API |
| RAG | 关键词命中手册 | pgvector + BGE-M3 |
| LLM | 无 Key 时确定性 Mock；有 Key 走 OpenAI 兼容接口 | 内网 vLLM + Qwen/GLM |
| 执行 | Mock Ansible Runner | ansible-runner + Git 版本化 Playbook |
| 密钥 | Mock Vault 30 分钟租约 | HashiCorp Vault |
| 备份 | 日报标记「未检查」 | restic / 引擎原生备份 + 隔离恢复 |
| 身份 | 单租户种子数据 | 企业 IAM |

## 目录

```
backend/app/     FastAPI、LangGraph、策略、执行器、Celery
backend/tests/   pytest
playbooks/        版本化预案白名单
knowledge/        已审核操作手册
frontend/         Vue 3 + Element Plus 工作台
scripts/demo.sh   三色路径演示
```

## 范围外（一期不做）

真实 Zabbix/Ansible/Vault/restic、Kubernetes、模型训练、完整备份产品、企业 IAM。

本仓库原先为 ERC4907 沙箱示例，已替换为 DevOpsAgent 一期工程。
