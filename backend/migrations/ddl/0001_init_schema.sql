-- =============================================================================
-- DevOpsAgent 数据库 DDL 文档
-- =============================================================================
-- 库：devops_agent（MySQL 8.0，utf8mb4）
-- 本文件与迁移版本 0001 对应：表结构初始化（20 张表）。
--
-- 【强约束】任何表结构变更（新增表/字段/索引/注释）都必须：
--   1. 修改 backend/app/models.py（字段必须带 comment=）
--   2. 生成并核对 alembic 迁移（backend/migrations/versions/）
--   3. 同步更新本目录下对应版本的 DDL 文件（字段必须带 COMMENT）
--   4. 在 ../README.md 的「迭代历史」表中登记一行
-- =============================================================================

SET NAMES utf8mb4;

-- =============================================================================
-- 一、业务表（13 张）
-- =============================================================================

-- 数字员工档案（数字员工身份与技能信息）
CREATE TABLE IF NOT EXISTS `digital_employees` (
  `id`              varchar(32)  NOT NULL                COMMENT '数字员工编号，如 DE-OPS-001',
  `name`            varchar(128) NOT NULL                COMMENT '数字员工名称',
  `team`            varchar(128) NOT NULL                COMMENT '所属运维团队',
  `systems`         json         NOT NULL                COMMENT '负责的系统列表（JSON 数组）',
  `manager`         varchar(64)  NOT NULL                COMMENT '责任经理',
  `oncall`          varchar(64)  NOT NULL                COMMENT '值班联系人',
  `skill_version`   varchar(64)  NOT NULL                COMMENT '技能包版本',
  `auth_expires_at` varchar(32)  NOT NULL                COMMENT '授权到期时间（字符串日期）',
  `status`          varchar(32)  NOT NULL                COMMENT '在岗状态：on_duty 在岗 / off_duty 离岗',
  `duties`          json         NOT NULL                COMMENT '职责清单（JSON 数组）',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数字员工档案';

-- 主机资产台账
CREATE TABLE IF NOT EXISTS `assets` (
  `id`              varchar(64)  NOT NULL                COMMENT '资产 ID',
  `hostname`        varchar(128) NOT NULL                COMMENT '主机名（唯一）',
  `app`             varchar(64)  NOT NULL                COMMENT '所属应用',
  `role`            varchar(64)  NOT NULL                COMMENT '主机角色，如 web/db/app',
  `env`             varchar(32)  NOT NULL                COMMENT '环境：prod/staging/test',
  `owner`           varchar(64)  NOT NULL                COMMENT '资产负责人',
  `tenant_id`       varchar(64)  NOT NULL                COMMENT '租户 ID',
  `reachable`       tinyint(1)   NOT NULL                COMMENT '网络是否可达：1 是 / 0 否',
  `db_ok`           tinyint(1)   NOT NULL                COMMENT '数据库探活是否正常：1 是 / 0 否',
  `last_restart_at` datetime     DEFAULT NULL            COMMENT '最近一次重启时间',
  `external_id`     varchar(64)  NOT NULL                COMMENT '外部系统（CMDB）资产 ID',
  `zabbix_host`     varchar(128) NOT NULL                COMMENT 'Zabbix 侧主机标识',
  `extra`           json         NOT NULL                COMMENT '扩展元数据（JSON 对象）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `hostname` (`hostname`),
  KEY `ix_assets_external_id` (`external_id`),
  KEY `ix_assets_tenant_id` (`tenant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='主机资产台账';

-- 维护窗口（窗口内告警不触发自动处置）
CREATE TABLE IF NOT EXISTS `maintenance_windows` (
  `id`        int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `asset_id`  varchar(64)  NOT NULL                COMMENT '资产 ID（fk → assets.id）',
  `reason`    varchar(256) NOT NULL                COMMENT '维护原因',
  `starts_at` datetime     NOT NULL                COMMENT '窗口开始时间（UTC）',
  `ends_at`   datetime     NOT NULL                COMMENT '窗口结束时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_maintenance_windows_asset_id` (`asset_id`),
  CONSTRAINT `maintenance_windows_ibfk_1` FOREIGN KEY (`asset_id`) REFERENCES `assets` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='维护窗口';

-- 任务单（自动/人工处置工单主表）
CREATE TABLE IF NOT EXISTS `tickets` (
  `id`                    int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `number`                varchar(32)  NOT NULL                COMMENT '任务单号（唯一，展示用）',
  `idempotency_key`       varchar(256) NOT NULL                COMMENT '幂等键（告警去重，唯一）',
  `source`                varchar(32)  NOT NULL                COMMENT '来源：zabbix/manual/demo',
  `status`                varchar(32)  NOT NULL                COMMENT '状态：pending_approval/approved/executing/succeeded/failed/escalated/rejected/cancelled',
  `employee_id`           varchar(32)  NOT NULL                COMMENT '处理本单的数字员工 ID',
  `asset_id`              varchar(64)  NOT NULL                COMMENT '目标资产 ID（fk → assets.id）',
  `tenant_id`             varchar(64)  NOT NULL                COMMENT '租户 ID',
  `title`                 varchar(256) NOT NULL                COMMENT '任务单标题',
  `trigger_name`          varchar(256) NOT NULL                COMMENT '触发告警名称',
  `severity`              varchar(32)  NOT NULL                COMMENT '告警级别',
  `action_type`           varchar(64)  NOT NULL                COMMENT '处置动作类型（预案动作 ID）',
  `job_version`           varchar(64)  NOT NULL                COMMENT '预案/作业版本',
  `event_id`              varchar(128) NOT NULL                COMMENT '原始告警事件 ID',
  `owner`                 varchar(64)  NOT NULL                COMMENT '当前责任人（数字员工或人工）',
  `risk_level`            varchar(16)  NOT NULL                COMMENT '风险等级：low/mid/high',
  `policy_light`          varchar(16)  DEFAULT NULL            COMMENT '策略灯：green 直接执行/yellow 需审批/red 禁止执行',
  `candidate_action_id`   varchar(64)  DEFAULT NULL            COMMENT '候选处置动作 ID',
  `playbook_version`      varchar(32)  DEFAULT NULL            COMMENT '使用的预案版本',
  `params`                json         NOT NULL                COMMENT '处置参数（JSON）',
  `params_digest`         varchar(64)  DEFAULT NULL            COMMENT '参数摘要（SHA256，供审批核对）',
  `diagnosis`             json         DEFAULT NULL            COMMENT 'LLM 诊断分析结果（JSON）',
  `evidence`              json         DEFAULT NULL            COMMENT '证据链快照（JSON）',
  `policy_result`         json         DEFAULT NULL            COMMENT '策略引擎判定结果（JSON）',
  `demo_scenario`         varchar(32)  NOT NULL                COMMENT '演示场景标记',
  `execution_count`       int          NOT NULL                COMMENT '已执行次数（重试累计）',
  `human_wait_seconds`    float        NOT NULL                COMMENT '人工审批等待时长（秒，时效统计用）',
  `approval_requested_at` datetime     DEFAULT NULL            COMMENT '发起审批时间（UTC）',
  `escalate_reason`       text                                 COMMENT '升级/拒绝原因说明',
  `created_at`            datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at`            datetime     NOT NULL                COMMENT '更新时间（UTC）',
  `closed_at`             datetime     DEFAULT NULL            COMMENT '关闭时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_tickets_idempotency_key` (`idempotency_key`),
  UNIQUE KEY `ix_tickets_number` (`number`),
  KEY `asset_id` (`asset_id`),
  KEY `ix_tickets_status` (`status`),
  CONSTRAINT `tickets_ibfk_1` FOREIGN KEY (`asset_id`) REFERENCES `assets` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='任务单';

-- 任务单事件流（全生命周期轨迹）
CREATE TABLE IF NOT EXISTS `ticket_events` (
  `id`         int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticket_id`  int         NOT NULL                COMMENT '任务单 ID（fk → tickets.id）',
  `kind`       varchar(64) NOT NULL                COMMENT '事件类型：created/diagnosed/executing/... ',
  `actor`      varchar(64) NOT NULL                COMMENT '事件发起方（数字员工/用户名/system）',
  `message`    text        NOT NULL                COMMENT '事件描述',
  `payload`    json        NOT NULL                COMMENT '事件附加数据（JSON）',
  `created_at` datetime    NOT NULL                COMMENT '事件时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_ticket_events_ticket_id` (`ticket_id`),
  CONSTRAINT `ticket_events_ibfk_1` FOREIGN KEY (`ticket_id`) REFERENCES `tickets` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='任务单事件流';

-- 人工审批单（yellow 灯动作需人工审批后执行）
CREATE TABLE IF NOT EXISTS `approvals` (
  `id`               int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticket_id`        int         NOT NULL                COMMENT '任务单 ID（fk → tickets.id）',
  `asset_id`         varchar(64) NOT NULL                COMMENT '目标资产 ID',
  `playbook_id`      varchar(64) NOT NULL                COMMENT '预案 ID',
  `playbook_version` varchar(32) NOT NULL                COMMENT '预案版本',
  `params_digest`    varchar(64) NOT NULL                COMMENT '参数摘要（SHA256）',
  `status`           varchar(16) NOT NULL                COMMENT '审批状态：pending/approved/rejected/expired',
  `approver`         varchar(64) DEFAULT NULL            COMMENT '审批人（用户名）',
  `comment`          text                                COMMENT '审批意见',
  `expires_at`       datetime    NOT NULL                COMMENT '审批截止时间（超时自动过期，UTC）',
  `created_at`       datetime    NOT NULL                COMMENT '创建时间（UTC）',
  `decided_at`       datetime    DEFAULT NULL            COMMENT '审批决定时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_approvals_ticket_id` (`ticket_id`),
  CONSTRAINT `approvals_ibfk_1` FOREIGN KEY (`ticket_id`) REFERENCES `tickets` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='人工审批单';

-- 审计链（不可变操作留痕）
CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id`               int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticket_id`        int         DEFAULT NULL            COMMENT '关联任务单 ID（可空）',
  `actor`            varchar(64) NOT NULL                COMMENT '操作者（用户名/数字员工/system）',
  `event_type`       varchar(64) NOT NULL                COMMENT '事件类型，如 action_executed/approved',
  `evidence_refs`    json        NOT NULL                COMMENT '证据引用（命令输出/日志位置，JSON）',
  `model_version`    varchar(64) DEFAULT NULL            COMMENT 'LLM 模型版本',
  `policy_version`   varchar(64) DEFAULT NULL            COMMENT '策略引擎版本',
  `playbook_version` varchar(64) DEFAULT NULL            COMMENT '预案版本',
  `approver`         varchar(64) DEFAULT NULL            COMMENT '审批人',
  `params_digest`    varchar(64) DEFAULT NULL            COMMENT '参数摘要（SHA256）',
  `result`           json        NOT NULL                COMMENT '执行结果（JSON）',
  `created_at`       datetime    NOT NULL                COMMENT '记录时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_audit_logs_event_type` (`event_type`),
  KEY `ix_audit_logs_ticket_id` (`ticket_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='审计链（不可变）';

-- 告警事件（Zabbix webhook 接入，按幂等键去重）
CREATE TABLE IF NOT EXISTS `alert_events` (
  `id`              int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `idempotency_key` varchar(256) NOT NULL                COMMENT '幂等键（唯一，防重复接入）',
  `event_id`        varchar(128) NOT NULL                COMMENT 'Zabbix 事件 ID',
  `asset_id`        varchar(64)  NOT NULL                COMMENT '关联资产 ID',
  `payload`         json         NOT NULL                COMMENT '告警原始报文（JSON）',
  `skipped`         tinyint(1)   NOT NULL                COMMENT '是否被跳过（维护窗口/重复等）：1 是 / 0 否',
  `skip_reason`     varchar(64)  DEFAULT NULL            COMMENT '跳过原因',
  `ticket_id`       int          DEFAULT NULL            COMMENT '生成的任务单 ID（fk → tickets.id）',
  `created_at`      datetime     NOT NULL                COMMENT '接入时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_alert_idempotency` (`idempotency_key`),
  KEY `ix_alert_events_idempotency_key` (`idempotency_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='告警事件';

-- 资产操作锁（同一资产同一时刻只允许一个处置动作）
CREATE TABLE IF NOT EXISTS `resource_locks` (
  `asset_id`     varchar(64) NOT NULL COMMENT '资产 ID（主键，一资产一锁）',
  `ticket_id`    int         NOT NULL COMMENT '持锁任务单 ID',
  `holder`       varchar(64) NOT NULL COMMENT '持锁方（数字员工/用户名）',
  `token`        varchar(64) NOT NULL COMMENT '锁令牌（释放/续期校验）',
  `acquired_at`  datetime    NOT NULL COMMENT '获锁时间（UTC）',
  `heartbeat_at` datetime    NOT NULL COMMENT '最近心跳时间（UTC）',
  `expires_at`   datetime    NOT NULL COMMENT '锁过期时间（UTC）',
  PRIMARY KEY (`asset_id`),
  KEY `ix_resource_locks_ticket_id` (`ticket_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='资产操作锁';

-- 动作失败熔断（连续失败的动作在冷却期内禁止重试）
CREATE TABLE IF NOT EXISTS `action_failures` (
  `asset_id`  varchar(64) NOT NULL COMMENT '资产 ID（联合主键）',
  `action_id` varchar(64) NOT NULL COMMENT '动作 ID（联合主键）',
  `reason`    text        NOT NULL COMMENT '最近一次失败原因',
  `failed_at` datetime    NOT NULL COMMENT '失败时间（UTC）',
  PRIMARY KEY (`asset_id`,`action_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='动作失败熔断';

-- 站内通知
CREATE TABLE IF NOT EXISTS `notifications` (
  `id`         int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ticket_id`  int          DEFAULT NULL            COMMENT '关联任务单 ID（可空）',
  `kind`       varchar(64)  NOT NULL                COMMENT '通知类型：approval_needed/executed/failed/...',
  `channel`    varchar(32)  NOT NULL                COMMENT '渠道：inbox/webhook',
  `title`      varchar(256) NOT NULL                COMMENT '通知标题',
  `body`       text         NOT NULL                COMMENT '通知正文',
  `payload`    json         NOT NULL                COMMENT '通知附加数据（JSON）',
  `read`       tinyint(1)   NOT NULL                COMMENT '是否已读：1 是 / 0 否',
  `created_at` datetime     NOT NULL                COMMENT '创建时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_notifications_kind` (`kind`),
  KEY `ix_notifications_ticket_id` (`ticket_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='站内通知';

-- 备份任务
CREATE TABLE IF NOT EXISTS `backup_jobs` (
  `id`                    varchar(64)  NOT NULL COMMENT '任务 ID',
  `name`                  varchar(128) NOT NULL COMMENT '任务名称',
  `asset_id`              varchar(64)  NOT NULL COMMENT '目标资产 ID',
  `schedule`              varchar(64)  NOT NULL COMMENT '调度周期（cron 表达式）',
  `enabled`               tinyint(1)   NOT NULL COMMENT '是否启用：1 是 / 0 否',
  `last_run_at`           datetime     DEFAULT NULL COMMENT '最近执行时间（UTC）',
  `last_backup_ok`        tinyint(1)   DEFAULT NULL COMMENT '最近备份结果：1 成功 / 0 失败 / NULL 未执行',
  `last_restore_verified` tinyint(1)   DEFAULT NULL COMMENT '最近恢复演练结果：1 通过 / 0 失败 / NULL 未演练',
  `note`                  text         NOT NULL COMMENT '备注',
  PRIMARY KEY (`id`),
  KEY `ix_backup_jobs_asset_id` (`asset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='备份任务';

-- 备份运行记录
CREATE TABLE IF NOT EXISTS `backup_runs` (
  `id`               int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `job_id`           varchar(64) NOT NULL                COMMENT '备份任务 ID（fk → backup_jobs.id）',
  `backup_ok`        tinyint(1)  DEFAULT NULL            COMMENT '备份结果：1 成功 / 0 失败',
  `restore_verified` tinyint(1)  DEFAULT NULL            COMMENT '恢复验证结果：1 通过 / 0 失败 / NULL 未验证',
  `status`           varchar(32) NOT NULL                COMMENT '运行状态：running/success/failed',
  `note`             text        NOT NULL                COMMENT '运行说明/错误信息',
  `started_at`       datetime    NOT NULL                COMMENT '开始时间（UTC）',
  `finished_at`      datetime    DEFAULT NULL            COMMENT '结束时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_backup_runs_job_id` (`job_id`),
  CONSTRAINT `backup_runs_ibfk_1` FOREIGN KEY (`job_id`) REFERENCES `backup_jobs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='备份运行记录';

-- =============================================================================
-- 二、RBAC 鉴权表（7 张）
-- =============================================================================

-- 用户
CREATE TABLE IF NOT EXISTS `users` (
  `id`              int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `username`        varchar(64)  NOT NULL                COMMENT '登录用户名（唯一）',
  `password_hash`   varchar(128) NOT NULL                COMMENT '密码哈希（bcrypt）',
  `display_name`    varchar(64)  NOT NULL                COMMENT '显示名称',
  `email`           varchar(128) DEFAULT NULL            COMMENT '邮箱（唯一）',
  `is_active`       tinyint(1)   NOT NULL                COMMENT '是否启用：1 启用 / 0 停用',
  `failed_attempts` int          NOT NULL                COMMENT '连续登录失败次数（成功后清零，达 5 次锁定）',
  `locked_until`    datetime     DEFAULT NULL            COMMENT '锁定截止时间（UTC，空=未锁定）',
  `last_login_at`   datetime     DEFAULT NULL            COMMENT '最近登录时间（UTC）',
  `last_login_ip`   varchar(64)  NOT NULL                COMMENT '最近登录 IP',
  `created_at`      datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at`      datetime     NOT NULL                COMMENT '更新时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_users_username` (`username`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户';

-- 角色
CREATE TABLE IF NOT EXISTS `roles` (
  `id`          int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `code`        varchar(32)  NOT NULL                COMMENT '角色编码（唯一）：admin/operator/viewer',
  `name`        varchar(64)  NOT NULL                COMMENT '角色名称',
  `description` varchar(256) NOT NULL                COMMENT '角色说明',
  `created_at`  datetime     NOT NULL                COMMENT '创建时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_roles_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色';

-- 权限点
CREATE TABLE IF NOT EXISTS `permissions` (
  `id`          int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `code`        varchar(64)  NOT NULL                COMMENT '权限编码（唯一），格式 资源:动作，如 tickets:operate',
  `name`        varchar(64)  NOT NULL                COMMENT '权限名称',
  `description` varchar(256) NOT NULL                COMMENT '权限说明',
  `created_at`  datetime     NOT NULL                COMMENT '创建时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_permissions_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='权限点';

-- 用户-角色 关联（多对多）
CREATE TABLE IF NOT EXISTS `user_roles` (
  `user_id` int NOT NULL COMMENT '用户 ID（联合主键，fk → users.id，级联删除）',
  `role_id` int NOT NULL COMMENT '角色 ID（联合主键，fk → roles.id，级联删除）',
  PRIMARY KEY (`user_id`,`role_id`),
  KEY `role_id` (`role_id`),
  CONSTRAINT `user_roles_ibfk_1` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE,
  CONSTRAINT `user_roles_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户-角色关联';

-- 角色-权限 关联（多对多）
CREATE TABLE IF NOT EXISTS `role_permissions` (
  `role_id`       int NOT NULL COMMENT '角色 ID（联合主键，fk → roles.id，级联删除）',
  `permission_id` int NOT NULL COMMENT '权限 ID（联合主键，fk → permissions.id，级联删除）',
  PRIMARY KEY (`role_id`,`permission_id`),
  KEY `permission_id` (`permission_id`),
  CONSTRAINT `role_permissions_ibfk_1` FOREIGN KEY (`permission_id`) REFERENCES `permissions` (`id`) ON DELETE CASCADE,
  CONSTRAINT `role_permissions_ibfk_2` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色-权限关联';

-- JWT 令牌白名单（jti 登记表，登出/改密吊销）
CREATE TABLE IF NOT EXISTS `user_tokens` (
  `jti`        varchar(36)  NOT NULL COMMENT 'JWT ID（主键，UUID）',
  `user_id`    int          NOT NULL COMMENT '所属用户 ID（fk → users.id，级联删除）',
  `issued_at`  datetime     NOT NULL COMMENT '签发时间（UTC）',
  `expires_at` datetime     NOT NULL COMMENT '过期时间（UTC）',
  `revoked_at` datetime     DEFAULT NULL COMMENT '吊销时间（UTC，空=有效）',
  `ip`         varchar(64)  NOT NULL COMMENT '签发时客户端 IP',
  `user_agent` varchar(255) NOT NULL COMMENT '签发时 User-Agent',
  PRIMARY KEY (`jti`),
  KEY `ix_user_tokens_expires_at` (`expires_at`),
  KEY `ix_user_tokens_user_id` (`user_id`),
  CONSTRAINT `user_tokens_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='JWT 令牌白名单';

-- 登录审计
CREATE TABLE IF NOT EXISTS `login_logs` (
  `id`          int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `username`    varchar(64)  NOT NULL                COMMENT '尝试登录的用户名',
  `user_id`     int          DEFAULT NULL            COMMENT '匹配到的用户 ID（用户不存在时为空）',
  `success`     tinyint(1)   NOT NULL                COMMENT '是否登录成功：1 成功 / 0 失败',
  `fail_reason` varchar(64)  NOT NULL                COMMENT '失败原因：bad_password/no_such_user/locked/rate_limited；成功为 none',
  `ip`          varchar(64)  NOT NULL                COMMENT '客户端 IP（X-Forwarded-For 真实 IP）',
  `user_agent`  varchar(255) NOT NULL                COMMENT 'User-Agent',
  `created_at`  datetime     NOT NULL                COMMENT '时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_login_logs_created_at` (`created_at`),
  KEY `ix_login_logs_ip` (`ip`),
  KEY `ix_login_logs_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='登录审计';

-- =============================================================================
-- 附：alembic_version 表由 Alembic 自动创建与管理（记录当前 schema 版本），
--     不在本 DDL 文档维护范围内。
-- =============================================================================
