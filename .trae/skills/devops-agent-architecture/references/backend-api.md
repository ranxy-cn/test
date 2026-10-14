# 后端 API 层（api.py + routers/）

入口 `backend/app/main.py`：lifespan 依次 `run_migrations()` → `seed_if_empty()` → `system_live.start_sampler()`；按顺序挂载 router：api → ops → auth → admin_users → admin_perms → dict → dashboard → knowledge → system_metrics → agent_api → ai。FastAPI CORS 全开。

## api.py（入口大路由）

- 健康/元数据：`GET /health`（集成模式描述）、`GET /api/v1/playbooks`（预案目录）、`GET /api/v1/employee/{id}`（数字员工档案+工单统计）
- **webhook** `POST /api/v1/webhooks/zabbix`：
  - `_check_webhook_secret` 校验 `X-Webhook-Secret` / `X-Zabbix-Token` / Bearer（hmac.compare_digest）
  - 按 `event_id` 幂等更新 `AnomalyEvent`（abnormal/recovered 两态），每条推送写 `AnomalyLog` 留痕
  - 首次异常自动 SSH 采集进程快照（`diagnostics.collect_for_anomaly`，`DIAGNOSTICS_ASYNC` 控制同步/线程）
  - **AI 联动**：`WEBHOOK_AUTO_TICKET` 立案后/AI 配置启用时，未恢复且非重复事件 → `ai_status=pending` + trigger 审计 + 派发 `analyze_anomaly_logs`（响应体含 `ai_dispatched`）；异常不影响 webhook 应答
  - 默认不自动立案；`WEBHOOK_AUTO_TICKET=true` 走 `_auto_ticket_from_webhook`（幂等键合并 → 维护窗口跳过 → 建 Ticket(pending_analysis) → `dispatch_investigation`）
  - `_infer_scenario` 按 trigger 关键词映射演示场景 green/yellow/red/verify_fail
- **tickets**：`GET /api/v1/tickets`（分页+母机/分组/关键字筛选）、`GET /tickets/{id}`（聚合 events/approvals/audit/lock/notifications）、`POST /tickets/{id}/approve`、`/reject`（409 冲突转译）
- **资产/母机**：资产 CRUD（删除前 `_assert_refs_free` 检查工单/维护窗/备份引用）、母机列表与总览、`POST /assets/mothers`（SSH 验证 → 建母机 → 自动纳管本机子机（唯一绑定 mother_id）→ 后台线程部署 agent；SSH 密码 Fernet 加密落库到母机与本机子机）、`POST /assets/provision` 一键纳管（**母机数据隔离**：复用已有 node-% 资产时若其 `mother_id` 已归属其他母机 → 409 拒绝并提示先在原母机下移除，防止子机数据被静默抢到新母机视角；机器升级为母机时 `create_mother` 带走自己的本机子机属正常语义；密码加密落库，幂等重跑时刷新）、分组管理、告警策略 CRUD（继承规则：子机自有 > 母机 > 默认）、CSV 导入导出、SSH 测试、实时巡检/sysinfo、`PATCH /assets/{id}` 支持 `ssh_password` 三态（缺省=不改 / 非空=加密保存 / 空串=清除，配置入口=新增/编辑子机页面）、`POST /assets/{id}/exec`（SSH 终端：wrapper 落 cd + `{ } 2>&1` 聚合 + 尾部 marker 回传 cwd/HOME/退出码；**凭据优先级：请求临时密码 > 资产保存的 SSH 密码（`_asset_ssh_password` 解密）> 免密巡检私钥兜底；无凭据且私钥不可用 400 引导编辑入口（密文存在但解密失败时单独提示「无法解密，请重新保存」，对应加密密钥变更场景）；认证失败 409 文案按凭据来源区分且不限制重试次数**，单条命令审计 `asset.ssh_exec` 不含密码，超时 20s）、`POST /assets/{id}/remove`（卸载 Agent 密码回退保存密码）、资产行 `_asset_row` 携带 `ssh_password_set` 布尔（永不回传明文）、指标 `trends`（落库+实时补尾+基线带+异常点+线性预测）
- **负责人告警推送**：`POST /assets/provision` 与 `PATCH /assets/{id}`（子机）**owner 必填**（空 400「负责人必填」，provision 走 Field(min_length=1) 422）；请求体可带 `owner_notify`（OwnerNotifyIn：channels 多选至少一项 + phone/email/dingtalk_webhook/feishu_webhook/wecom_webhook），经 `services/owner_notify.validate_notify_payload` 校验（所选渠道地址必填、webhook 须 https、邮箱格式）后 `upsert_contact` 按 owner 姓名落 `owner_contacts`（与资产同事务）；`GET /api/v1/assets/owner-contacts?q=` 联想列表（须注册在 `/assets/{asset_id}` 路由之前）；编辑子机选已有负责人自动回填渠道配置；母机编辑不强制推送配置（已有 channels 才提交）
- **异常**：`GET /api/v1/anomalies`、`/stats`（北京时区 KPI+7 天趋势）、`/{aid}` 详情（含 `ai_status`）、`POST /{aid}/snapshot` 手动重采
- **ai.py**（/api/v1/ai，AI 日志分析）：
  - `GET/PUT /ai/settings`（`ai:config`）：配置读写；api_key Fernet 加密落库、读取一律脱敏（`has_api_key`+`api_key_tail` 末 4 位）；`keep_api_key=true` 且不传新 key 表示沿用
  - `POST /ai/settings/test`（`ai:config`）：连接测试，请求体可临时覆盖 base_url/api_key/model；成功失败均写 connection_test 审计；无密钥时提示缺失项（分析任务无密钥会走 mock，测试按钮必须真实密钥）
  - `GET /ai/analyses`（`ai:view`）：分页+anomaly_id/status/severity/tag/q 筛选，`order=time|severity`（severity 为相关性排序，Python 权重）；返回 `anomalies` 关联块供双向跳转
  - `GET /ai/analyses/{id}`（`ai:view`）：详情含 `raw_response`（留痕原文）+ `context_digest` + `anomaly` 块
  - `POST /ai/analyses/{id}/feedback`（`ai:feedback`）：人工处理记录（handled_by/note/at）+ feedback 审计
  - `POST /ai/anomalies/{anomaly_id}/analyze`（`ai:feedback`）：手动（重）触发分析，未启用 400；`_dispatch` 按 `USE_CELERY` 走 `apply_async(priority=告警级别映射)` 或同步
- 其他：`GET /api/v1/audit`、`GET /api/v1/reports/daily`（日报；「已通知人工 ≠ 业务已恢复」）

## routers/ 各文件

- **ops.py**（prefix=/api/v1）：`POST /actions/run` 人工一键白名单预案（红灯 409）；压测工具组 `/tools/*`（`STRESS_TOOLS_ENABLED` 开关）；`GET /status` 集成状态；资源锁 CRUD + `/locks/retry-queued`；`POST /tickets/{id}/retry-execution`；通知列表/已读；备份组 `/backups`（run / verify-restore / run-due）
- **auth.py**（/api/v1/auth）：`POST /login` 三层防护（IP 10 分钟 30 次失败→429；用户 5 次失败锁 15 分钟→423；不存在用户也做哈希抹平时延）、logout、`GET /me`、change-password
- **admin_users.py**（/api/v1/admin）：users 列表/新建/修改/解锁（权限 `users:manage`）
- **admin_perms.py**：菜单树 CRUD（dir/menu/button 三型）、角色-菜单授权 `/menus-flat` + `PUT /roles/{id}/menus`、权限点管理（`roles:manage` / `menus:manage`）
- **dict.py**：`GET /api/v1/dict?type=` 业务字典（无权限点，供下拉/中文名映射）
- **dashboard.py**：大屏聚合路由，无缓存（曾用 DASHBOARD_CACHE/TTLCache 10 分钟降噪，用户要求取消已整体摘除，每次请求实时计算；netdata 为逐台 SSH/HTTP 探测开销大，调用方自行控制轮询频率）：
  - `GET /dashboard/overview`：大屏 KPI 聚合
  - `GET /dashboard/topology`（`dashboard:read`）：资产关联拓扑——mothers（kind=mother，children=已挂载子机）+ 孤立子机（children=[]）；节点含 id/hostname/app/group/kind/reachable/db_ok/ip（extra.provision.ip || extra.deploy.ip）；payload 含 generated_at/groups
  - `GET /dashboard/stats`（`dashboard:read`）：大屏效能统计聚合；payload：`mttr`（avg/p50 分钟=closed_at−created_at、recovered_count、saved_hours 按每单人工基线 0.5h 折算）、`automation`（auto_recovered=已恢复且无 Approval 记录 / approved_recovered / escalated / open 未终态数 / rate=自动恢复占比）、`funnel`（8 态计数）、`policy`（green/yellow/red 灯计数 + red_reasons 按 escalate_reason 分组 Top4）、`alerts`（近 7 天按 first_seen_at：total/recovered/recover_rate、top_assets 按 hostname 帕累托 Top6、heatmap 北京时间 7×24 星期×小时矩阵）、`mtta`（立案→首次采证/诊断/执行事件，avg_minutes+count）、`dwell`（analysis/approval/executing/verifying 四环节平均滞留小时，按 TicketEvent kind 对配，含在途区间末端=now）、`approval`（avg_wait_minutes=decided_at−created_at、expired=expired 或 pending 且过期、pending、approvers Top3）、`playbooks`（items 按 candidate_action_id||action_type 分组 total/recovered/escalated/open/cooldown/success_rate（名字取 load_catalog），cooldown_total=reasons/escalate_reason 含"冷却"，verify_first_pass_rate=验证后恢复/（恢复+升级））、`ai`（adoption_rate=diagnosis 非空且 candidate_action_id 非空占比、diagnosed_count、blind_spots=escalated 且 reason 含"未命中"按 trigger_name Top3）、`capacity`（predictions=近 3 天 SystemMetricSample+MetricSample 的 disk% 线性外推到 95% 且 ≤60 天 Top3、aging_count/aging_hosts=last_restart_at 空或超 180 天）、`scores`（map=asset_id→0-100 健康分扣分制：不可达-40/db 坏-15/告警-3×N≤24/在途工单-8×N≤24/磁盘预测<14d-15 否则-8/未重启超 180d-5 下限 5；worst=最低 5 台含 kind/hostname/score/reasons）。实现口径：tickets/events/approvals 全量载入 Python 聚合（表量级小）
  - `GET /dashboard/assets/{asset_id}/sysinfo`（`dashboard:read`）：大屏 3D 点击服务器 → Agent 低频上报的全景详情缓存（复用 agent_api.sysinfo_of，与资产监控 POST /assets/{id}/sysinfo 同数据口径；权限走 dashboard:read 以免大屏账号需要 assets:read）；无缓存返回 503
  - `GET /assets/{id}/netdata`
- **knowledge.py**：文档列表/上传/删除、`GET /knowledge/search` 检索、`POST /knowledge/chat` AI 对话（`knowledge:chat`）、`POST /knowledge/chat/stream` **流式问答（SSE）**：`_chat_messages` 与同步版共享上下文组装，事件流 meta（模型/知识命中/实时状态）→ delta（文本增量）→ done（source=ai|fallback）；AI 网关异常时兜底摘要整段经 delta 发出，响应头 `X-Accel-Buffering: no` 关 nginx 缓冲（services/knowledge.chat_completion_stream 走 OpenAI 兼容 stream=True）、`POST /knowledge/data-query` 智能数据问答（`knowledge:chat`；见 services/nl2sql.py 四层安全：业务表白名单——users/roles/permissions/menus/user_tokens/login_logs/app_settings(AI 密钥) 等非业务表拒绝；单库限定——库名前缀必须等于本系统库、系统库拒绝；只读——sqlglot AST 校验仅 SELECT + 拦写语法/多语句/危险函数(SLEEP/BENCHMARK/LOAD_FILE)/INTO OUTFILE/FOR UPDATE/占位符 + MySQL `START TRANSACTION READ ONLY` 只读事务兜底（结束自动还原不残留）；资源保护——LIMIT 自动补/收敛（裸浏览 200、带过滤 1000）+ `MAX_EXECUTION_TIME(8000)` hint；LLM 生成 SQL + 结果解读两段复用 chat_completion，无 AI 时关键词 mock 兜底（资产/告警/工单/通知/负责人））
- **system_metrics.py**：`GET /system/metrics/realtime`（无 asset_id=本机 /proc；带 asset_id=agent 最新帧，母机解析到本机子机）、`GET /system/metrics/history`（≤2h 原始 5s / ≤2 天 5 分钟桶 / 更长 1 小时桶）
- **agent_api.py**（自研子机 Agent 接入）：
  - `POST /agent/report`：`X-Agent-Token` 认证（compare_digest），支持断网补发批量；最新帧存进程内 `LATEST` 字典
  - `GET /agent/config`：配置三级合并（内置默认 ← app_settings 全局 ← 资产 `extra.agent_config`），改后免重启
  - 资产级 token/status/config 管理、全局默认配置读写、`POST /assets/{id}/agent/deploy`
  - ext 扩展字段白名单收敛防注入
- **ai.py**：AI 日志分析全端点（配置加密/连接测试/分析列表详情/人工处理/手动触发），详见上方 api.py 段；权限点 `ai:view`(SELF_SERVICE) / `ai:config`(admin) / `ai:feedback`(operator+admin)
- **deps.py**：`get_current_user`（Bearer → JWT 校验 → UserToken jti 白名单（revoked/过期拒绝）→ 加载用户）、`require_perm(*codes)`（全部权限点须满足否则 403）

## 约定

- 新增端点优先放进对应 routers/；与工单/资产核心流转强相关的仍放 api.py（历史约定）
- 所有业务接口需鉴权；仅 /login、/health、webhook、agent report 走独立认证
