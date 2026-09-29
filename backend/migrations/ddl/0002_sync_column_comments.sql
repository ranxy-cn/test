-- =============================================================================
-- DevOpsAgent DDL 变更脚本
-- =============================================================================
-- 版本：0002（对应迁移 versions/0002_sync_column_comments.py）
-- 目的：为线上全量表与字段回填 COMMENT（列定义与线上完全一致，仅补注释，无结构变化）
-- 约定：本文件由迁移 0002 在 MySQL 上自动执行；后续每次表结构变更，
--       必须在本目录新增对应编号的 DDL 变更文件并同步迁移脚本。
-- =============================================================================

SET NAMES utf8mb4;

-- ---- 业务表 ----

ALTER TABLE `digital_employees`
  COMMENT='数字员工档案',
  MODIFY `id` varchar(32) NOT NULL COMMENT '数字员工编号，如 DE-OPS-001',
  MODIFY `name` varchar(128) NOT NULL COMMENT '数字员工名称',
  MODIFY `team` varchar(128) NOT NULL COMMENT '所属运维团队',
  MODIFY `systems` json NOT NULL COMMENT '负责的系统列表（JSON 数组）',
  MODIFY `manager` varchar(64) NOT NULL COMMENT '责任经理',
  MODIFY `oncall` varchar(64) NOT NULL COMMENT '值班联系人',
  MODIFY `skill_version` varchar(64) NOT NULL COMMENT '技能包版本',
  MODIFY `auth_expires_at` varchar(32) NOT NULL COMMENT '授权到期时间（字符串日期）',
  MODIFY `status` varchar(32) NOT NULL COMMENT '在岗状态：on_duty 在岗 / off_duty 离岗',
  MODIFY `duties` json NOT NULL COMMENT '职责清单（JSON 数组）';

ALTER TABLE `assets`
  COMMENT='主机资产台账',
  MODIFY `id` varchar(64) NOT NULL COMMENT '资产 ID',
  MODIFY `hostname` varchar(128) NOT NULL COMMENT '主机名（唯一）',
  MODIFY `app` varchar(64) NOT NULL COMMENT '所属应用',
  MODIFY `role` varchar(64) NOT NULL COMMENT '主机角色，如 web/db/app',
  MODIFY `env` varchar(32) NOT NULL COMMENT '环境：prod/staging/test',
  MODIFY `owner` varchar(64) NOT NULL COMMENT '资产负责人',
  MODIFY `tenant_id` varchar(64) NOT NULL COMMENT '租户 ID',
  MODIFY `reachable` tinyint(1) NOT NULL COMMENT '网络是否可达：1 是 / 0 否',
  MODIFY `db_ok` tinyint(1) NOT NULL COMMENT '数据库探活是否正常：1 是 / 0 否',
  MODIFY `last_restart_at` datetime DEFAULT NULL COMMENT '最近一次重启时间',
  MODIFY `external_id` varchar(64) NOT NULL COMMENT '外部系统（CMDB）资产 ID',
  MODIFY `zabbix_host` varchar(128) NOT NULL COMMENT 'Zabbix 侧主机标识',
  MODIFY `extra` json NOT NULL COMMENT '扩展元数据（JSON 对象）';

ALTER TABLE `maintenance_windows`
  COMMENT='维护窗口',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '资产 ID（fk → assets.id）',
  MODIFY `reason` varchar(256) NOT NULL COMMENT '维护原因',
  MODIFY `starts_at` datetime NOT NULL COMMENT '窗口开始时间（UTC）',
  MODIFY `ends_at` datetime NOT NULL COMMENT '窗口结束时间（UTC）';

ALTER TABLE `tickets`
  COMMENT='任务单',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `number` varchar(32) NOT NULL COMMENT '任务单号（唯一，展示用）',
  MODIFY `idempotency_key` varchar(256) NOT NULL COMMENT '幂等键（告警去重，唯一）',
  MODIFY `source` varchar(32) NOT NULL COMMENT '来源：zabbix/manual/demo',
  MODIFY `status` varchar(32) NOT NULL COMMENT '状态：pending_approval/approved/executing/succeeded/failed/escalated/rejected/cancelled',
  MODIFY `employee_id` varchar(32) NOT NULL COMMENT '处理本单的数字员工 ID',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '目标资产 ID（fk → assets.id）',
  MODIFY `tenant_id` varchar(64) NOT NULL COMMENT '租户 ID',
  MODIFY `title` varchar(256) NOT NULL COMMENT '任务单标题',
  MODIFY `trigger_name` varchar(256) NOT NULL COMMENT '触发告警名称',
  MODIFY `severity` varchar(32) NOT NULL COMMENT '告警级别',
  MODIFY `action_type` varchar(64) NOT NULL COMMENT '处置动作类型（预案动作 ID）',
  MODIFY `job_version` varchar(64) NOT NULL COMMENT '预案/作业版本',
  MODIFY `event_id` varchar(128) NOT NULL COMMENT '原始告警事件 ID',
  MODIFY `owner` varchar(64) NOT NULL COMMENT '当前责任人（数字员工或人工）',
  MODIFY `risk_level` varchar(16) NOT NULL COMMENT '风险等级：low/mid/high',
  MODIFY `policy_light` varchar(16) DEFAULT NULL COMMENT '策略灯：green 直接执行/yellow 需审批/red 禁止执行',
  MODIFY `candidate_action_id` varchar(64) DEFAULT NULL COMMENT '候选处置动作 ID',
  MODIFY `playbook_version` varchar(32) DEFAULT NULL COMMENT '使用的预案版本',
  MODIFY `params` json NOT NULL COMMENT '处置参数（JSON）',
  MODIFY `params_digest` varchar(64) DEFAULT NULL COMMENT '参数摘要（SHA256，供审批核对）',
  MODIFY `diagnosis` json DEFAULT NULL COMMENT 'LLM 诊断分析结果（JSON）',
  MODIFY `evidence` json DEFAULT NULL COMMENT '证据链快照（JSON）',
  MODIFY `policy_result` json DEFAULT NULL COMMENT '策略引擎判定结果（JSON）',
  MODIFY `demo_scenario` varchar(32) NOT NULL COMMENT '演示场景标记',
  MODIFY `execution_count` int NOT NULL COMMENT '已执行次数（重试累计）',
  MODIFY `human_wait_seconds` float NOT NULL COMMENT '人工审批等待时长（秒，时效统计用）',
  MODIFY `approval_requested_at` datetime DEFAULT NULL COMMENT '发起审批时间（UTC）',
  MODIFY `escalate_reason` text COMMENT '升级/拒绝原因说明',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）',
  MODIFY `updated_at` datetime NOT NULL COMMENT '更新时间（UTC）',
  MODIFY `closed_at` datetime DEFAULT NULL COMMENT '关闭时间（UTC）';

ALTER TABLE `ticket_events`
  COMMENT='任务单事件流',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `ticket_id` int NOT NULL COMMENT '任务单 ID（fk → tickets.id）',
  MODIFY `kind` varchar(64) NOT NULL COMMENT '事件类型：created/diagnosed/executing/...',
  MODIFY `actor` varchar(64) NOT NULL COMMENT '事件发起方（数字员工/用户名/system）',
  MODIFY `message` text NOT NULL COMMENT '事件描述',
  MODIFY `payload` json NOT NULL COMMENT '事件附加数据（JSON）',
  MODIFY `created_at` datetime NOT NULL COMMENT '事件时间（UTC）';

ALTER TABLE `approvals`
  COMMENT='人工审批单',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `ticket_id` int NOT NULL COMMENT '任务单 ID（fk → tickets.id）',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '目标资产 ID',
  MODIFY `playbook_id` varchar(64) NOT NULL COMMENT '预案 ID',
  MODIFY `playbook_version` varchar(32) NOT NULL COMMENT '预案版本',
  MODIFY `params_digest` varchar(64) NOT NULL COMMENT '参数摘要（SHA256）',
  MODIFY `status` varchar(16) NOT NULL COMMENT '审批状态：pending/approved/rejected/expired',
  MODIFY `approver` varchar(64) DEFAULT NULL COMMENT '审批人（用户名）',
  MODIFY `comment` text COMMENT '审批意见',
  MODIFY `expires_at` datetime NOT NULL COMMENT '审批截止时间（超时自动过期，UTC）',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）',
  MODIFY `decided_at` datetime DEFAULT NULL COMMENT '审批决定时间（UTC）';

ALTER TABLE `audit_logs`
  COMMENT='审计链（不可变）',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `ticket_id` int DEFAULT NULL COMMENT '关联任务单 ID（可空）',
  MODIFY `actor` varchar(64) NOT NULL COMMENT '操作者（用户名/数字员工/system）',
  MODIFY `event_type` varchar(64) NOT NULL COMMENT '事件类型，如 action_executed/approved',
  MODIFY `evidence_refs` json NOT NULL COMMENT '证据引用（命令输出/日志位置，JSON）',
  MODIFY `model_version` varchar(64) DEFAULT NULL COMMENT 'LLM 模型版本',
  MODIFY `policy_version` varchar(64) DEFAULT NULL COMMENT '策略引擎版本',
  MODIFY `playbook_version` varchar(64) DEFAULT NULL COMMENT '预案版本',
  MODIFY `approver` varchar(64) DEFAULT NULL COMMENT '审批人',
  MODIFY `params_digest` varchar(64) DEFAULT NULL COMMENT '参数摘要（SHA256）',
  MODIFY `result` json NOT NULL COMMENT '执行结果（JSON）',
  MODIFY `created_at` datetime NOT NULL COMMENT '记录时间（UTC）';

ALTER TABLE `alert_events`
  COMMENT='告警事件',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `idempotency_key` varchar(256) NOT NULL COMMENT '幂等键（唯一，防重复接入）',
  MODIFY `event_id` varchar(128) NOT NULL COMMENT 'Zabbix 事件 ID',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '关联资产 ID',
  MODIFY `payload` json NOT NULL COMMENT '告警原始报文（JSON）',
  MODIFY `skipped` tinyint(1) NOT NULL COMMENT '是否被跳过（维护窗口/重复等）：1 是 / 0 否',
  MODIFY `skip_reason` varchar(64) DEFAULT NULL COMMENT '跳过原因',
  MODIFY `ticket_id` int DEFAULT NULL COMMENT '生成的任务单 ID（fk → tickets.id）',
  MODIFY `created_at` datetime NOT NULL COMMENT '接入时间（UTC）';

ALTER TABLE `resource_locks`
  COMMENT='资产操作锁',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '资产 ID（主键，一资产一锁）',
  MODIFY `ticket_id` int NOT NULL COMMENT '持锁任务单 ID',
  MODIFY `holder` varchar(64) NOT NULL COMMENT '持锁方（数字员工/用户名）',
  MODIFY `token` varchar(64) NOT NULL COMMENT '锁令牌（释放/续期校验）',
  MODIFY `acquired_at` datetime NOT NULL COMMENT '获锁时间（UTC）',
  MODIFY `heartbeat_at` datetime NOT NULL COMMENT '最近心跳时间（UTC）',
  MODIFY `expires_at` datetime NOT NULL COMMENT '锁过期时间（UTC）';

ALTER TABLE `action_failures`
  COMMENT='动作失败熔断',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '资产 ID（联合主键）',
  MODIFY `action_id` varchar(64) NOT NULL COMMENT '动作 ID（联合主键）',
  MODIFY `reason` text NOT NULL COMMENT '最近一次失败原因',
  MODIFY `failed_at` datetime NOT NULL COMMENT '失败时间（UTC）';

ALTER TABLE `notifications`
  COMMENT='站内通知',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `ticket_id` int DEFAULT NULL COMMENT '关联任务单 ID（可空）',
  MODIFY `kind` varchar(64) NOT NULL COMMENT '通知类型：approval_needed/executed/failed/...',
  MODIFY `channel` varchar(32) NOT NULL COMMENT '渠道：inbox/webhook',
  MODIFY `title` varchar(256) NOT NULL COMMENT '通知标题',
  MODIFY `body` text NOT NULL COMMENT '通知正文',
  MODIFY `payload` json NOT NULL COMMENT '通知附加数据（JSON）',
  MODIFY `read` tinyint(1) NOT NULL COMMENT '是否已读：1 是 / 0 否',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）';

ALTER TABLE `backup_jobs`
  COMMENT='备份任务',
  MODIFY `id` varchar(64) NOT NULL COMMENT '任务 ID',
  MODIFY `name` varchar(128) NOT NULL COMMENT '任务名称',
  MODIFY `asset_id` varchar(64) NOT NULL COMMENT '目标资产 ID',
  MODIFY `schedule` varchar(64) NOT NULL COMMENT '调度周期（cron 表达式）',
  MODIFY `enabled` tinyint(1) NOT NULL COMMENT '是否启用：1 是 / 0 否',
  MODIFY `last_run_at` datetime DEFAULT NULL COMMENT '最近执行时间（UTC）',
  MODIFY `last_backup_ok` tinyint(1) DEFAULT NULL COMMENT '最近备份结果：1 成功 / 0 失败 / NULL 未执行',
  MODIFY `last_restore_verified` tinyint(1) DEFAULT NULL COMMENT '最近恢复演练结果：1 通过 / 0 失败 / NULL 未演练',
  MODIFY `note` text NOT NULL COMMENT '备注';

ALTER TABLE `backup_runs`
  COMMENT='备份运行记录',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `job_id` varchar(64) NOT NULL COMMENT '备份任务 ID（fk → backup_jobs.id）',
  MODIFY `backup_ok` tinyint(1) DEFAULT NULL COMMENT '备份结果：1 成功 / 0 失败',
  MODIFY `restore_verified` tinyint(1) DEFAULT NULL COMMENT '恢复验证结果：1 通过 / 0 失败 / NULL 未验证',
  MODIFY `status` varchar(32) NOT NULL COMMENT '运行状态：running/success/failed',
  MODIFY `note` text NOT NULL COMMENT '运行说明/错误信息',
  MODIFY `started_at` datetime NOT NULL COMMENT '开始时间（UTC）',
  MODIFY `finished_at` datetime DEFAULT NULL COMMENT '结束时间（UTC）';

-- ---- RBAC 鉴权表 ----

ALTER TABLE `users`
  COMMENT='用户',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `username` varchar(64) NOT NULL COMMENT '登录用户名（唯一）',
  MODIFY `password_hash` varchar(128) NOT NULL COMMENT '密码哈希（bcrypt）',
  MODIFY `display_name` varchar(64) NOT NULL COMMENT '显示名称',
  MODIFY `email` varchar(128) DEFAULT NULL COMMENT '邮箱（唯一）',
  MODIFY `is_active` tinyint(1) NOT NULL COMMENT '是否启用：1 启用 / 0 停用',
  MODIFY `failed_attempts` int NOT NULL COMMENT '连续登录失败次数（成功后清零，达 5 次锁定）',
  MODIFY `locked_until` datetime DEFAULT NULL COMMENT '锁定截止时间（UTC，空=未锁定）',
  MODIFY `last_login_at` datetime DEFAULT NULL COMMENT '最近登录时间（UTC）',
  MODIFY `last_login_ip` varchar(64) NOT NULL COMMENT '最近登录 IP',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）',
  MODIFY `updated_at` datetime NOT NULL COMMENT '更新时间（UTC）';

ALTER TABLE `roles`
  COMMENT='角色',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `code` varchar(32) NOT NULL COMMENT '角色编码（唯一）：admin/operator/viewer',
  MODIFY `name` varchar(64) NOT NULL COMMENT '角色名称',
  MODIFY `description` varchar(256) NOT NULL COMMENT '角色说明',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）';

ALTER TABLE `permissions`
  COMMENT='权限点',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `code` varchar(64) NOT NULL COMMENT '权限编码（唯一），格式 资源:动作，如 tickets:operate',
  MODIFY `name` varchar(64) NOT NULL COMMENT '权限名称',
  MODIFY `description` varchar(256) NOT NULL COMMENT '权限说明',
  MODIFY `created_at` datetime NOT NULL COMMENT '创建时间（UTC）';

ALTER TABLE `user_roles`
  COMMENT='用户-角色关联',
  MODIFY `user_id` int NOT NULL COMMENT '用户 ID（联合主键，fk → users.id，级联删除）',
  MODIFY `role_id` int NOT NULL COMMENT '角色 ID（联合主键，fk → roles.id，级联删除）';

ALTER TABLE `role_permissions`
  COMMENT='角色-权限关联',
  MODIFY `role_id` int NOT NULL COMMENT '角色 ID（联合主键，fk → roles.id，级联删除）',
  MODIFY `permission_id` int NOT NULL COMMENT '权限 ID（联合主键，fk → permissions.id，级联删除）';

ALTER TABLE `user_tokens`
  COMMENT='JWT 令牌白名单',
  MODIFY `jti` varchar(36) NOT NULL COMMENT 'JWT ID（主键，UUID）',
  MODIFY `user_id` int NOT NULL COMMENT '所属用户 ID（fk → users.id，级联删除）',
  MODIFY `issued_at` datetime NOT NULL COMMENT '签发时间（UTC）',
  MODIFY `expires_at` datetime NOT NULL COMMENT '过期时间（UTC）',
  MODIFY `revoked_at` datetime DEFAULT NULL COMMENT '吊销时间（UTC，空=有效）',
  MODIFY `ip` varchar(64) NOT NULL COMMENT '签发时客户端 IP',
  MODIFY `user_agent` varchar(255) NOT NULL COMMENT '签发时 User-Agent';

ALTER TABLE `login_logs`
  COMMENT='登录审计',
  MODIFY `id` int NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  MODIFY `username` varchar(64) NOT NULL COMMENT '尝试登录的用户名',
  MODIFY `user_id` int DEFAULT NULL COMMENT '匹配到的用户 ID（用户不存在时为空）',
  MODIFY `success` tinyint(1) NOT NULL COMMENT '是否登录成功：1 成功 / 0 失败',
  MODIFY `fail_reason` varchar(64) NOT NULL COMMENT '失败原因：bad_password/no_such_user/locked/rate_limited；成功为 none',
  MODIFY `ip` varchar(64) NOT NULL COMMENT '客户端 IP（X-Forwarded-For 真实 IP）',
  MODIFY `user_agent` varchar(255) NOT NULL COMMENT 'User-Agent',
  MODIFY `created_at` datetime NOT NULL COMMENT '时间（UTC）';
