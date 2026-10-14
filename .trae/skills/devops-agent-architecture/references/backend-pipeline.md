# 后端业务层（services/ + domain/ + agent/ + executor/ + integrations/ + workers/）

## services/ 核心

- **pipeline.py**（主流程编排，改业务先读它）：
  - `dispatch_investigation` / `dispatch_execution`：`USE_CELERY=true` 走 celery，否则同步
  - `create_manual_ticket`：人工工单与告警链路共用策略引擎
  - `run_pipeline`：owner 通知 → `run_investigation`(LangGraph) → `parse_diagnosis` 安全校验 → `evaluate_policy` → 绿=run_execution / 黄=`_request_approval`（Approval 24h 过期）+ 通知 / 红=escalate；任何异常兜底 escalate
  - `run_execution`：状态须 pending_execution 且 `execution_count>=1` 禁二次 → 取资产锁（失败排队 lock_queued）→ `validate_tool_call` → playbook 每步 heartbeat+事件 → 结果脱敏落 evidence → 失败记 ActionFailure+escalate → `BusinessProbeVerifier` 探测（失败 escalate）→ recovered
  - `approve_ticket`：校验过期 / params_digest 绑定 / 资产匹配；`reject_ticket` → escalate；`retry_queued_executions` 补发无锁排队单；`_escalate` 终态幂等
- **alert_engine.py**（指标越限告警引擎 v2）：celery beat 每分钟 `run_alert_cycle`。持续类=窗口内全部样本越限且样本数≥2；事件型（OOM/关键进程/端口探活）=窗口内出现过即异常、整窗干净自动恢复。`_upsert_event` 按 event_id 前缀 `metric-<asset_id>-<rule_key>` 定位打开中的事件：持续 abnormal 复用同一行刷新，恢复后再触发**新建一条**（event_id 追加时刻后缀），支持 `notify_minutes` 重复提醒。规则集：BASE_METRICS（cpu/mem/load）、EXT_METRICS（swap/inode/disk_io/网络/TCP）、应用/DB 规则（agent ext.metrics 数据源）。扫描范围=child + 有上报样本的 mother。**AI 联动**：周期尾对启用且 ai_status 未在 pending/running/done 的新事件置 pending 并派发 `analyze_anomaly_logs`（priority 按级别映射），统计 `ai_dispatched`
- **alert_policy.py**：P0-P3 级别（P0 红 / P1 橙 / P2 黄 / P3 蓝灰）；`DEFAULT_POLICY`（cpu/mem 90% 300s=P1、load 1.5；process/port 默认 P0 但 enabled=False）；优先级 子机>母机>平台默认；v2 秒制窗口兼容 v1 分钟×60
- **locks.py**：`ResourceLock` 以 asset_id 为主键的单资产互斥锁；TTL 默认 120s 过期自动清理；heartbeat 续期；冲突 → 排队事件+审计
- **notify.py**：Inbox / Log / Webhook 三实现，`build_notifiers`（始终 inbox+log，配置 NOTIFY_WEBHOOK_URL 才加 webhook）；事件五类：ticket_created / pending_approval / recovered / escalated / lock_queued
- **owner_notify.py**（负责人告警推送，2026-10 新增）：`owner_contacts` 联系人按资产 owner 姓名匹配，渠道 `sms/email/dingtalk/feishu/wecom` 多选。`notify_alert(db, anomaly_id)` 新告警推送（级别/主机/告警/负责人/value/threshold + 「AI 分析完成后另行推送」）；`notify_ai_analysis(db, anomaly_id)` AI done 后推送（结论/诊断截 400/建议前 3 条）；`_dispatch` 按渠道顺序逐个 try、单渠道失败不影响其他、逐渠道写 `notifications` 留痕（kind=owner_alert/owner_ai_analysis，channel=owner-{渠道}，payload.notify={ok,reason}）。短信 `_send_sms` 为 stub（`SMS_PROVIDER` 空返回 ok=True「短信供应商未对接，消息已生成待发送」），消息生成与留痕逻辑完整；邮箱走 SMTP（未配置降级失败留痕）；钉钉/飞书/企微 webhook httpx 5s。**三个触发点**：alert_engine.evaluate 尾部新事件、zabbix webhook 首次异常（duplicate/恢复不推）、ai_analyzer.run_analysis 成功 done 后
- **cooldowns.py**：ActionFailure 熔断表；`action_fail_cooldown_violated` 按默认 1800s 判定
- **agent_deployer.py**：SSH 下发自研 agent（py/go）。流程=连 SSH → sudo 探测 → py 版检测 python3≥3.7（可自动安装）→ sftp 原子上传 → pkill 旧进程 → `pty=False` nohup setsid 拉起（PTY 会话关闭会杀子进程）→ 10s 内等首帧上报验证。进度写 `asset.extra["agent_deploy"]`
- **provision.py**：纯 SSH 远程执行库（连接/PTY 流式日志/sudo 探测）；`asset_id_for_ip` 生成 `node-<ip>-<port>`；ProvisionError 可读错误转译
- **metrics_store.py**：采集优先级 agent>LATEST → 母机 /proc → mock；5 分钟槽幂等落库 MetricSample；`query_series`（降采样≤720 点）、`query_compare` 多日对齐、`build_baseline` 日内时段 P05~P95、`detect_anomalies`、`forecast_series` 线性回归；mock 演示序列也在此（api/celery 共用）
- **system_live.py**：母机本机 /proc 后台线程 5s 采样落 `system_metric_samples`，保留 60 天（支持 /host/proc 宿主挂载）
- **diagnostics.py**：异常时刻进程快照——单条 SSH 聚合命令（@@MARK@@ 切段）采 TOP 进程/D 状态/监听端口/会话，写 `anomaly_events.diagnostics`
- **inspector.py**：SSH 免密巡检（内置私钥 /app/keys/inspect_key；密码即用即弃），top 进程/内存/磁盘/负载 + sysinfo 全景
- **probe.py**：TCP 探活周期任务（默认 22 口，16 线程），回写 reachable/last_seen/unreachable_reason
- **stress.py**：真实 CPU/内存压测（fork 死循环子进程/逐页触达内存，自带 deadline），`STRESS_TOOLS_ENABLED` 开关
- **netdata.py**：Netdata REST 只读适配，仅访问资产登记 IP（防 SSRF）
- **backups.py**：BackupJob/BackupRun mock 占位；备份成功与恢复验证分开记
- **tickets.py**：工单号 `TKT-YYYYMMDD-NNNN`（北京时区）；幂等键 `event_id|asset_id|job_version|action_type`
- **audit.py**：`add_event` 写 ticket_events 事件流、`add_audit` 写 audit_logs 审计链
- **knowledge.py**：知识文档解析（md/docx 等）、`redact_sensitive` 脱敏、关键词打分检索，供 routers/knowledge.py 与 AI 对话使用；`chat_completion_stream` 为 async 生成器（httpx.AsyncClient stream=True + aiter_lines 解析 OpenAI 兼容 SSE），直通事件循环免线程池抖动
- **nl2sql.py**（智能数据问答，2026-10 新增）：NL→SQL→只读执行→AI 解读四段管线；`ALLOWED_TABLES` 24 张业务表白名单（含中文说明，inspector 反射列名带缓存）；`validate_and_guard` 基于 sqlglot（MySQL 方言）做 AST 安全审计；`execute_readonly` MySQL 走 `START TRANSACTION READ ONLY`+ROLLBACK（连接归还池无残留），sqlite 直连；查询留痕走 `nl2sql` logger（拦截/成功/失败均记 operator+question+sql）。依赖 sqlglot==25.24.5（requirements.txt）
- **ai_analyzer.py**（AI 日志分析核心服务，改 AI 链路先读它）：
  - **配置**：`CONFIG_KEY=ai_analyzer_config` 存 AppSetting；默认 enabled=False，base_url/api_key 回落 chat→openai 环境配置；api_key Fernet 加密落库（密钥从 JWT_SECRET sha256 派生，可用 AI_ENCRYPT_KEY 覆盖）；`masked_config` 只露末 4 位
  - **优先级映射**：`SEVERITY_PRIORITY` P0→0/P1→1/P2→6/P3→9（critical/high/warning 别名），celery `apply_async(priority=n)` 关键告警先分析
  - **预处理** `gather_context`：告警信息+payload 关键键+window_series 首末各 10 点+进程快照（截 10）+通知留痕 5 条+MetricSample 0.5h 各系列末 15 点，总长截 6000 字符——只含日志数据与问题描述
  - **调用** `_chat`：OpenAI 兼容 /chat/completions，temperature 0、json_object、Bearer、超时=配置；无 api_key → `_mock_response` 确定性输出（model 带 mock- 前缀，供无密钥演示/负载测试）
  - **解析** `parse_ai_response`：剥 markdown fence/抓首尾大括号→JSON；severity 白名单、causes 截 8×300、solutions 保留 dict 且 title 非空截 8、confidence clamp
  - **内容过滤**：`_BLOCK_PATTERNS` 16 组正则（rm -rf/reboot/mkfs/dd if=/fork 炸弹/chmod 777/iptables/systemctl restart/kill/ssh/eval(/base64 -d/curl http/DROP TABLE/apt install/cat /etc/shadow 等），命中字段替换「【已屏蔽：疑似服务器操作指令】」→ status=blocked；原始响应始终留痕 raw_response
  - **编排** `run_analysis(anomaly_id)`：幂等（pending/running 直接返回）→ 建记录 running → 未启用 skipped → 分析 → 清洗落库 done/blocked → 审计；异常 failed+error+重试由外层 celery 任务承担，兜底 except 保证不留僵尸 running

## domain/（纯领域层，无 IO 依赖）

- **catalog.py**：`Playbook` dataclass（risk/auto_allowed/requires_db_ok/requires_reachable/restart_cooldown/allowed_params/steps/success_criteria）；`load_catalog` 从 `playbooks/catalog.yaml` 加载（lru_cache）
- **policy.py**：纯代码策略引擎（LLM 不参与）。`evaluate_policy` 顺序：维护窗口→红；预案未命中→红；前置失败（不可达/依赖 DB 异常/重启冷却/失败冷却）→红；`risk==high or not auto_allowed`→黄；否则绿。版本 `policy-v1.0.0`
- **safety.py**：`validate_diagnosis_against_catalog`（action 必须在目录、参数不超 allowed_params）、`validate_tool_call`（TOOL-* 白名单+跨租户拒绝）、`never_give_secrets_to_llm`（递归剔除 password/token/secret 等 9 类键）、`cooldown_violated`
- **state_machine.py**：`ALLOWED` 迁移表 + `TERMINAL`（recovered/skipped/escalated）；非法迁移抛 `InvalidTransition`

## agent/（LangGraph 排查）

- **graph.py**：`InvestigationState` TypedDict；线性三节点 `START→rag→gather→diagnose→END`；模块级缓存编译图
- **llm.py**：SYSTEM_PROMPT 硬约束（不输出命令/凭据，candidate_action_id 只能是预案编号）。`OPENAI_API_KEY` 未配置→`mock_diagnose` 确定性诊断；配置→OpenAI Chat（temperature 0.1、json_object、证据先脱敏）→失败 fallback 回 mock（版本号+"+fallback"）。mock 场景映射：yellow/replication→ACT-DB-FAILOVER、disk→ACT-CLEAN-TMPLOG、probe→ACT-RESTART-PROBE、red/未知→null action（升级）、默认→ACT-ROLLING-RESTART
- **rag.py**：关键词打分式简易 RAG（扫 knowledge/*.md，阈值 0.4，top3）；查询含「未知/mystery/native crash/coredump」直接返回空
- **tools.py**：`INVESTIGATION_TOOLS` 白名单 6 个 TOOL-* 编号及参数域；REDACT_PATTERNS 日志脱敏；`gather_evidence` 5 类证据（metrics/events/logs/deploys/deps）线程池并行，每个任务过白名单+租户校验

## executor/

- **ansible.py / vault.py**：兼容一期导入的转发壳（实际实现都在 integrations/）
- **verifier.py**：`BusinessProbeVerifier`——连续 `probe_required_passes`（默认 3）次通过、间隔 1s；`verify_fail` 场景必失败；经 `observation_seconds`（默认 10s）观察期复测，复发返回 recurrence=true 且**不循环重启**

## integrations/（Adapter 协议 + mock/real 工厂）

- **protocols.py**：`VaultClient`（health/issue_lease/revoke）与 `PlaybookRunner`（health/run，带 on_step 回调）两个 Protocol
- **\_\_init\_\_.py 工厂**：`_item_mode`（ANSIBLE_MODE/VAULT_MODE 分项覆盖全局 INTEGRATION_MODE，非法值回 mock）；`_ansible_ready`（需 ANSIBLE_RUNNER_ENABLED 或 real/auto，且 ansible-runner 可导入或二进制存在）；`describe_integrations`（requested vs effective + fallback_reason，供 /status 页）；`get_vault_client` / `get_playbook_runner`
- **ansible/**：mock.py（MockPlaybookRunner，只跑版本化 playbook 拒绝自由命令）；real.py（优先 ansible-runner 库否则 ansible-playbook 子进程，临时 private_data_dir+inventory）；safety.py 结果脱敏
- **vault/**：mock.py MockVaultClient（带 TTL）；http.py HttpVaultClient（KV lease）

## workers/

- **celery_app.py**：broker/backend=Redis；`task_acks_late=True`、prefetch=1。beat_schedule 5 个定时任务：retry_queued_tickets 每 15s、run_due_backups 每 60s、probe_assets 每 300s、collect_metric_samples 每 300s（5 分钟槽）、scan_alerts 每 60s
- **tasks.py**：8 个任务：investigate_ticket、execute_ticket、retry_queued_tickets、probe_assets、collect_metric_samples、scan_alerts、run_due_backups（均 max_retries=0）+ **analyze_anomaly_logs**（max_retries=3，指数退避 30s/60s/120s，`task_var.set("ai_analysis")`，调 ai_analyzer.run_analysis；重试耗尽 log.exception 不再抛）
