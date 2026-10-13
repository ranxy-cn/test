# 后端数据层（models / schemas / config / security / seed / database / migrations / tests）

## models.py（20+ 张表）

RBAC 与身份：`User`（防爆破字段 failed_attempts/locked_until）、`Role`、`Permission`（模块:动作 命名）、`UserRole`、`RolePermission`、`Menu`（dir/menu/button 资源树）、`RoleMenu`、`DictEntry`（dict_type+code 唯一）、`UserToken`（jti 白名单，支持吊销）、`LoginLog`

业务：`DigitalEmployee`（DE-OPS-001 小维）、`Asset`（hostname 唯一；kind=mother/child；mother_id；db_mode；`extra` JSON 扩展大杂烩：agent_config/agent_deploy/netdata/provision 等）、`AppSetting`（KV 全局配置）、`MaintenanceWindow`、`Ticket`（idempotency_key 唯一；policy_light/candidate_action_id/diagnosis/evidence/policy_result JSON；execution_count；demo_scenario）、`TicketEvent`、`Approval`（params_digest 绑定 + expires_at）、`AuditLog`（evidence_refs/model_version/policy_version/playbook_version）、`AlertEvent`（幂等键唯一）、`AnomalyEvent`（event_id 唯一，两态 abnormal/recovered，diagnostics 快照+diag_status，`ai_status` AI 分析状态 none/pending/running/done/blocked/failed/skipped）、`AnomalyLog`（原始通知留痕）、`MetricSample`（(asset_id,ts) 唯一，5 分钟趋势）、`SystemMetricSample`（5s 实时+ext JSON）、`ResourceLock`（asset_id 主键）、`ActionFailure`（(asset_id,action_id) 复合主键）、`Notification`、`BackupJob`/`BackupRun`、`KnowledgeDocument`（正文 MEDIUMTEXT 落库）

AI 日志分析：`AiAnalysis`（anomaly_id FK+index；status pending/running/done/blocked/failed/skipped；priority 告警级别映射；severity/summary/diagnosis/causes JSON/solutions JSON[{title,detail,tag,severity}]；confidence/model/latency_ms；blocked 命中过滤标记；error；raw_response MEDIUMTEXT 留痕；context_digest sha256 前 16；handled_by/note/at 人工处理）、`AiAuditLog`（action=config_update/connection_test/trigger/success/failed/blocked/skipped/feedback；operator/model/latency_ms/ok/detail JSON）

工具：`BEIJING_TZ`、`utcnow`；`TicketStatus` 8 态枚举、`TicketSource`（alert/schedule/assign/manual）

## schemas.py（入参/出参约束）

- `ACTION_ID_RE=^ACT-[A-Z0-9-]+$`、`ASSET_ID_RE=^ast-[a-z0-9-]+$`
- `FORBIDDEN_PARAM_KEYS`：cmd/shell/script/path 等 11 个禁用参数键
- `params_digest` = sha256(asset_id + playbook_version + params 规范化 JSON)，审批绑定用
- `ToolCall`：action_id 必须 ACT-/TOOL- 前缀，拒绝 `; | \` $( rm sudo /bin/ bash -`；参数值拒绝命令/路径片段
- `Diagnosis`：candidate_action_id 只能是预案编号格式，禁 commands/shell 字段
- `ZabbixWebhookIn`：兼容旧字段与 Zabbix 5.0 宏（severity 映射表）
- `AiSettingsOut/In`（keep_api_key 默认 True 沿用已存密钥；Out 只含 has_api_key/api_key_tail 脱敏字段）、`AiConnectionTestOut`（ok/message/model/latency_ms/mock）、`AiAnalysisOut`（from_attributes）、`AiSolutionItem`（title/detail/tag/severity）、`AiFeedbackIn`（note 1~2000 字）

## config.py

`Settings`（.env + 环境变量，`get_settings` lru_cache）。关键默认：database_url MySQL devops_agent、webhook_secret="dev-webhook-secret"、**webhook_auto_ticket=False**、use_celery=False、demo_mode=True、diagnostics_async=True、jwt_secret 空→进程随机、access_token 720 分钟、lockout 5 次/15 分钟、IP 30 次/10 分钟、admin_initial_password="Admin@123456"、seed_demo_assets=False（生产默认关；tests/conftest.py 强制注入 true，防止空库被种入 ast-order-* 演示资产）、observation_seconds=10、probe_required_passes=3、restart/action_fail 冷却各 1800s、lock_ttl 120s、integration_mode="mock"、policy_version="policy-v1.0.0"、tenant_id="tenant-default"、employee_id="DE-OPS-001"、mother_asset_id="devops-mother-186"、netdata 开 19999、openai/chat 双套 LLM 配置（chat 为企业网关）

## security.py

bcrypt(rounds=12)；密码策略 8-64 位含大小写数字；JWT HS256（iss/aud 固定、jti、leeway 5s）；jwt_secret 未配置用进程随机（重启即失效）；`encrypt_secret/decrypt_secret`：Fernet 可逆加密（密钥口径同 ai_analyzer：AI_ENCRYPT_KEY → JWT_SECRET → fallback），用于资产 SSH 密码落库，空串=未配置语义

## 种子（只补缺不覆盖）

- **seed.py**：`PERMISSIONS` 24 个权限点目录（含 ai:view/ai:config/ai:feedback）；3 角色 viewer/operator/admin 权限矩阵（ai:view 进 SELF_SERVICE_PERMS 全角色可见，ai:feedback 给 operator+admin，ai:config 仅 admin）；`seed_if_empty` 种数字员工档案 + 演示资产 9 台（`SEED_DEMO_ASSETS` 且资产表为空才种，含 ast-order-app-01/02/03、db、redis、lb、gw、job、unreachable）+ 维护窗口 + 备份任务 + 初始 admin；RBAC 菜单授权只初始化一次，新菜单增量补
- **dict_seed.py**：trigger/asset/action 三类中文字典 18 项
- **menus_seed.py**：`MENU_TREE` 菜单/按钮资源树（与迁移 0003 共用）

## database.py

`TZDateTime` TypeDecorator（写转 UTC 去 tz、读补 UTC）；sqlite 内存库 StaticPool 共享单连接；MySQL 池 10+20；`run_migrations`：sqlite 走 create_all，MySQL 走 alembic upgrade head；`init_engine` 供测试重绑

## migrations/（Alembic）

- **双目录**：`versions/`（18 个 .py，0001~0014 语义编号 + 4 个 hash 编号）与 `ddl/`（对应 .sql **权威 DDL 文档**）
- **0013_ai_log_analysis**：anomaly_events 加 `ai_status` 列 + 建 `ai_analyses`（3 索引）/`ai_audit_logs`（4 索引），InnoDB utf8mb4；sqlite 测试库跳过走 create_all
- **0015_asset_ssh_password**：assets 加 `ssh_password_enc`（String 512 NOT NULL DEFAULT ''，Fernet 密文）；存量资产空串=未配置，行为不变
- **0016_owner_contacts**：建 `owner_contacts`（负责人联系人：name 唯一索引、phone/email/dingtalk_webhook/feishu_webhook/wecom_webhook 默认空串、channels JSON 多选渠道、updated_at），InnoDB utf8mb4；sqlite 测试库跳过走 create_all；模型 `OwnerContact`（Asset 类之后），api.py `upsert_contact` 按 owner 姓名与资产提交同事务落库
- **config.py 新增**：`smtp_host/port(465)/user/password/sender/use_ssl(True)`（邮箱推送，未配置降级失败留痕）、`sms_provider=""`（短信 stub 开关：空=未对接，消息生成+留痕照走）
- **四步强流程**（README 定死）：改模型（字段带 comment）→ 写迁移（编号顺延）→ 更新 ddl/ SQL（每字段/每表必须 COMMENT）→ 登记迭代历史表
- `env.py`：sys.path 注入 backend/ 后 `from app import models` 注册全部表；URL 优先 alembic.ini（database.py 会 set_main_option），否则 DATABASE_URL

## tests/

运行：`cd backend && pytest`（pytest.ini：testpaths=tests、pythonpath=.，29 个测试文件；test_ai_analyzer.py 35 例覆盖配置加密/解析/内容过滤/优先级映射/mock 端到端/webhook 与 alert_engine 联动）

conftest.py 要点（新增测试依赖这些前提）：
- 模块级强制环境变量：`DATABASE_URL=sqlite:///:memory:`、USE_CELERY=false、**DIAGNOSTICS_ASYNC=false**（StaticPool 单连接下后台线程会 StaleDataError）、OBSERVATION/PROBE_INTERVAL=0、INTEGRATION_MODE=mock、SEED_DEMO_ASSETS=true、**WEBHOOK_AUTO_TICKET=true**（测试保留自动立案管线）、PLAYBOOKS_DIR/KNOWLEDGE_DIR 指仓库根；剔除 ANSIBLE_MODE/VAULT_MODE 防容器环境渗入
- 禁读开发者 .env + `get_settings.cache_clear()`
- `client` fixture：TestClient + 真实签发的 admin JWT（走生产同款 jti 白名单路径）
- autouse `_reset_db`：每用例 drop_all+create_all+seed_if_empty，并清 `load_catalog` 缓存
- `auth_headers()` 返回 webhook 密钥头
