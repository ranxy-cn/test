-- 0014: 告警恢复任务（自定义恢复脚本表 / 恢复任务表）
-- 与 app/models.py RecoveryScript / RecoveryTask、alembic 0014 保持一致；可手工执行（IF NOT EXISTS 幂等）。

CREATE TABLE IF NOT EXISTS `recovery_scripts` (
  `id`              int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `name`            varchar(64)  NOT NULL                COMMENT '脚本名称',
  `description`     varchar(256) NOT NULL DEFAULT ''    COMMENT '用途说明',
  `rule_key`        varchar(64)  NOT NULL DEFAULT ''    COMMENT '匹配的告警规则 key（空=全部规则）',
  `risk_level`      varchar(8)   NOT NULL DEFAULT 'low' COMMENT '风险等级：low 低风险（可自动执行）/ high 高风险（需人工确认）',
  `command`         text                                 COMMENT 'Shell 恢复命令（目标机 root 执行）',
  `timeout_seconds` int          NOT NULL DEFAULT 60    COMMENT '执行超时（秒）',
  `enabled`         tinyint(1)   NOT NULL DEFAULT 1     COMMENT '是否启用',
  `created_at`      datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at`      datetime     NOT NULL                COMMENT '更新时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_recovery_scripts_rule_key` (`rule_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='自定义恢复脚本（告警联动自动/人工执行）';

CREATE TABLE IF NOT EXISTS `recovery_tasks` (
  `id`             int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `anomaly_id`     int          NOT NULL                COMMENT '关联异常告警 ID',
  `event_id`       varchar(128) NOT NULL                COMMENT '告警 event_id（幂等键）',
  `asset_id`       varchar(64)  NOT NULL DEFAULT ''    COMMENT '资产 ID（空=未关联资产）',
  `rule_key`       varchar(64)  NOT NULL DEFAULT ''    COMMENT '告警规则 key',
  `severity`       varchar(32)  NOT NULL DEFAULT ''    COMMENT '告警级别（P0~P3）',
  `priority`       int          NOT NULL DEFAULT 50    COMMENT '优先级分 0~99，越大越紧急（P 级映射基础分，持续未恢复叠加）',
  `status`         varchar(16)  NOT NULL DEFAULT 'open' COMMENT 'open 待处理 / executing 执行中 / done 完成 / cancelled 已取消',
  `script_id`      int          DEFAULT NULL            COMMENT '命中的恢复脚本（空=纯人工任务）',
  `script_name`    varchar(64)  NOT NULL DEFAULT ''    COMMENT '脚本名快照',
  `executed_by`    varchar(64)  NOT NULL DEFAULT ''    COMMENT '执行人（system=自动执行）',
  `execute_ok`     tinyint(1)   DEFAULT NULL            COMMENT '最近一次脚本执行结果',
  `execute_output` text                                 COMMENT '脚本输出留痕',
  `resolved_at`    datetime     DEFAULT NULL            COMMENT '关闭时间（UTC）',
  `resolve_reason` varchar(128) NOT NULL DEFAULT ''    COMMENT '关闭原因',
  `created_at`     datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at`     datetime     NOT NULL                COMMENT '更新时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_recovery_tasks_anomaly_id` (`anomaly_id`),
  KEY `ix_recovery_tasks_event_id` (`event_id`),
  KEY `ix_recovery_tasks_priority` (`priority`),
  KEY `ix_recovery_tasks_status` (`status`),
  KEY `ix_recovery_tasks_created_at` (`created_at`),
  CONSTRAINT `fk_recovery_tasks_anomaly` FOREIGN KEY (`anomaly_id`) REFERENCES `anomaly_events` (`id`),
  CONSTRAINT `fk_recovery_tasks_script` FOREIGN KEY (`script_id`) REFERENCES `recovery_scripts` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='告警恢复任务（告警触发生成，恢复自动关闭）';
