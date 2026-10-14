---
name: devops-agent-architecture
description: 新知AI-智能巡检 project architecture map and per-module business logic reference (backend, frontend, data, ops). Use when starting any task in this repo to avoid rescanning the codebase, when asked 项目架构 or 模块业务逻辑, or after code iterations that add or change routers, services, models, views, playbooks, workers or env vars. Not for deployment, use deploy-103 instead.
---

# 新知AI-智能巡检 项目架构地图

本仓库 = 智能运维数字员工（7×24 值守）：接收告警 → 编号任务单 → 采集证据 → AI 根因诊断 → 纯代码策略引擎决定自动修复 / 审批 / 升级 → 只跑固定 Playbook → 业务探测验证 → 审计与日报。

**安全红线（代码强制）**：LLM 只做分析与建议，不持凭据、不发明命令（`candidate_action_id` 必须来自 `playbooks/` 白名单）；策略引擎是纯代码非模型；高风险动作必须人工审批（绑定 params_digest）；验证失败一律升级，不循环重启。

## 任务开始怎么用本 skill

1. 按「模块导航」定位任务所在的层，只读对应 `references/*.md`，**不要全量扫描代码库**。
2. 细节以 references 为准；仅对入口文件做少量 Read 确认即可动手。
3. 代码迭代完成后，按「维护规则」同步更新对应 reference 文件（这是硬要求）。
4. **代码更新后的收尾（硬要求，用户已授权）**：每次代码更新完成并验证通过后，必须依次执行：
   - `git commit` 提交本次改动（**只 commit 不 push**，除非用户明确要求 push）；
   - 部署到 **103** 生产（日常唯一部署目标）：`bash .trae/skills/deploy-103/scripts/deploy-103.sh`（流程见 deploy-103 skill；186 已于 2026-10-14 停用，勿再部署）。

   两条都要做，不许只做其一；commit 信息用中文概括「改了什么 + 为什么」。

## 技术栈

- 后端 `backend/`：Python 3.11 + FastAPI + SQLAlchemy + Alembic + Celery/Redis + LangGraph；生产 MySQL 8，测试 sqlite 内存库
- 前端 `frontend/`：Vue 3 + Element Plus + ECharts + Vite + axios；nginx 反代 `/api` → `api:8000`
- 执行链：ansible-runner / ansible-playbook（默认 mock）+ Vault 短凭证（默认 mock）
- 部署：docker compose（api / worker / beat / frontend / redis；MySQL 为可选 profile，默认用宿主机 MySQL）

## 目录地图（顶层）

```
backend/app/
  api.py            入口大路由：webhook、tickets 流转、资产/母机、异常、审计、日报
  routers/          拆分路由：ops / auth / admin_users / admin_perms / dict / dashboard /
                    knowledge / system_metrics / agent_api（+ deps 鉴权依赖）
  services/         业务服务：pipeline 主流程、alert_engine、locks、notify、metrics_store、
                    agent_deployer、provision、inspector、diagnostics 等
  domain/           纯领域层：catalog 预案目录、policy 策略引擎、safety 安全校验、state_machine
  agent/            LangGraph 排查：graph（rag→gather→diagnose）/ llm / rag / tools
  executor/         ansible、vault 转发壳 + verifier 业务探测
  integrations/     Adapter 协议 + mock/real 工厂（ansible / vault）
  workers/          celery_app（beat 5 个定时任务）+ tasks（7 个任务）
  models.py / schemas.py / config.py / security.py / seed.py / database.py
frontend/src/       views/ 18 个页面 + components/ 4 个 + api.js / auth.js / router.js
playbooks/          预案白名单：catalog.yaml + 4 个预案元数据 yaml + ansible/ 真实 yml
knowledge/          内置 runbook（RAG 语料，与上传文档同源检索）
backend/migrations/ alembic versions/（16 个迁移）+ ddl/（权威 SQL 文档）
scripts/            demo.sh 三色演示、deploy.sh、ansible_ping
```

## 核心主线（必须记住）

**告警两条入口**：
1. 自研子机 Agent `POST /api/v1/agent/report` → `alert_engine` 每分钟判定 → 写 `anomaly_events`（只记异常，不自动立案）
2. 外部 webhook `POST /api/v1/webhooks/zabbix` → `AnomalyEvent` 两态（abnormal/recovered）留痕；`WEBHOOK_AUTO_TICKET=true` 时才自动立案建 Ticket

**Ticket 管线**（`pipeline.run_pipeline`）：
立案 `pending_analysis` → LangGraph（rag→gather→diagnose）产出受白名单约束的 Diagnosis → `policy.evaluate_policy` 出灯：
- 绿（低风险 + 白名单 + 前置满足 + 不在冷却）→ 自动 `run_execution`
- 黄（high 风险或未开放自动）→ 挂 Approval（params_digest 绑定，24h 过期）等人批
- 红（未命中预案 / 前置失败 / 维护窗口 / 失败冷却）→ `escalated` 终态；管线异常兜底也 escalate

**执行**（`pipeline.run_execution`）：取 `ResourceLock`（asset 粒度 TTL 120s，冲突排队 `lock_queued`）→ `validate_tool_call` → PlaybookRunner（mock/real 按 INTEGRATION_MODE 工厂注入）→ BusinessProbeVerifier 连续 3 次探测通过 + 10s 观察期 → `recovered`；任何失败 → `escalated`。全程 TicketEvent + AuditLog 双写。

**状态机 8 态**：`pending_analysis → pending_approval → pending_execution → executing → verifying → recovered | escalated`（另有 skipped；recovered/skipped/escalated 为终态，非法迁移抛 InvalidTransition）。

**白名单预案 4 个**：ACT-ROLLING-RESTART（low/auto）、ACT-DB-FAILOVER（high/需审批）、ACT-CLEAN-TMPLOG（low/auto）、ACT-RESTART-PROBE（low/auto）。

## 关键开关与默认值

| 变量 | 默认 | 说明 |
|---|---|---|
| INTEGRATION_MODE | mock | real 缺凭据自动回退 mock；ANSIBLE_MODE/VAULT_MODE 可分项覆盖 |
| WEBHOOK_AUTO_TICKET | false | webhook 自动立案开关（生产关，测试 conftest 开） |
| USE_CELERY | 本地 false / compose true | false 时 pipeline 同步执行 |
| OPENAI_API_KEY | 空 | 空 → mock_diagnose 确定性诊断；配置 → 真实 LLM（失败回退 mock） |
| JWT_SECRET | 空 | 空 → 进程随机密钥，重启后登录失效 |
| OBSERVATION_SECONDS | 10 | 观察期（生产建议 600） |
| LOCK_TTL_SECONDS | 120 | 资源锁 TTL |
| ACTION_FAIL_COOLDOWN_SECONDS / RESTART_COOLDOWN_SECONDS | 1800 | 失败冷却 / 重启冷却 |
| SEED_DEMO_ASSETS | true | 资产表为空时种 9 台演示资产 |

端口：api 8000（/docs、/health、/api/v1/status）、frontend 8080→80。

## 模块导航（按任务类型选读）

| 要改什么 | 读哪个文件 |
|---|---|
| API 端点 / 路由 / webhook / 鉴权依赖 | references/backend-api.md |
| pipeline / 策略 / 状态机 / LangGraph / 执行器 / 集成工厂 / celery / 各 service | references/backend-pipeline.md |
| 数据模型 / 新迁移 / 配置 / 种子 / 测试约定 | references/backend-data.md |
| 页面 / 组件 / 前端路由 / api.js / 权限体系 | references/frontend.md |
| 预案 / runbook / 脚本 / compose / CI / 环境变量 | references/ops-infra.md |

## 维护规则（代码迭代后必须执行）

按改动类型更新对应文件；只改受影响小节；事实必须带文件路径，**不写行号**（易失效）：

| 改动 | 更新 |
|---|---|
| 新增/删除/修改 router、端点、鉴权方式 | references/backend-api.md |
| 新增/修改 service、celery 任务、状态机、策略、agent 图、执行器、集成适配 | references/backend-pipeline.md |
| 新增模型 / alembic 迁移 / 配置项 / 种子数据 / 测试基建 | references/backend-data.md |
| 新增/删除页面、组件、前端路由、权限点 | references/frontend.md |
| 新增预案、runbook、脚本、compose 服务、环境变量、CI 步骤 | references/ops-infra.md |
| 顶层新增目录/服务、技术栈变化、核心主线变化 | 本文件 SKILL.md |

禁止向 references 堆易碎细节（行号、大段代码）；保持「文件路径 + 职责 + 关键规则」粒度。
