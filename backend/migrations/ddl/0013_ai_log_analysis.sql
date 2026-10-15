-- 0013: AI 日志分析（分析结果表 / AI 审计表 / 异常表 AI 状态列）
-- 与 app/models.py AiAnalysis / AiAuditLog / AnomalyEvent.ai_status、alembic 0013 保持一致；可手工执行（IF NOT EXISTS 幂等）。

ALTER TABLE `anomaly_events`
  ADD COLUMN `ai_status` varchar(16) NOT NULL DEFAULT 'none'
  COMMENT 'AI 日志分析状态：none 未触发 / pending 已入队 / running 分析中 / done 完成 / failed 失败 / skipped 未启用';

CREATE TABLE IF NOT EXISTS `ai_analyses` (
  `id`             int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `anomaly_id`     int          NOT NULL                COMMENT '关联异常告警 ID',
  `status`         varchar(16)  NOT NULL DEFAULT 'pending' COMMENT 'pending 排队 / running 分析中 / done 完成 / blocked 响应被屏蔽 / failed 失败 / skipped 未启用',
  `priority`       int          NOT NULL DEFAULT 5      COMMENT '分析优先级 0~9（P0→0 最高）',
  `severity`       varchar(16)  NOT NULL DEFAULT ''     COMMENT 'AI 判定严重程度：critical/high/medium/low/info',
  `summary`        text                                  COMMENT '一句话结论',
  `diagnosis`      text                                  COMMENT '问题诊断',
  `causes`         json                                  COMMENT '可能原因列表',
  `solutions`      json                                  COMMENT '解决方案列表 [{title,detail,tag,severity}]',
  `confidence`     double       NOT NULL DEFAULT 0      COMMENT '置信度 0~1',
  `model`          varchar(128) NOT NULL DEFAULT ''     COMMENT '模型名（mock-* 表示演示模式）',
  `latency_ms`     int          NOT NULL DEFAULT 0      COMMENT 'AI 请求耗时（毫秒）',
  `blocked`        tinyint(1)   NOT NULL DEFAULT 0      COMMENT '响应是否命中操作指令过滤',
  `error`          varchar(1024) NOT NULL DEFAULT ''    COMMENT '失败原因',
  `raw_response`   mediumtext                            COMMENT '模型原始响应（审计留痕）',
  `context_digest` varchar(64)  NOT NULL DEFAULT ''     COMMENT '送审上下文指纹（sha256 前 16 位）',
  `handled_by`     varchar(64)  NOT NULL DEFAULT ''     COMMENT '人工处理人',
  `handled_note`   text                                  COMMENT '人工处理备注',
  `handled_at`     datetime     DEFAULT NULL            COMMENT '人工处理时间（UTC）',
  `created_at`     datetime     NOT NULL                COMMENT '创建时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_ai_analyses_anomaly_id` (`anomaly_id`),
  KEY `ix_ai_analyses_status` (`status`),
  KEY `ix_ai_analyses_created_at` (`created_at`),
  CONSTRAINT `fk_ai_analyses_anomaly` FOREIGN KEY (`anomaly_id`) REFERENCES `anomaly_events` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI 日志分析结果（与异常告警双向关联）';

CREATE TABLE IF NOT EXISTS `ai_audit_logs` (
  `id`          int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `anomaly_id`  int          DEFAULT NULL            COMMENT '关联异常告警 ID',
  `analysis_id` int          DEFAULT NULL            COMMENT '关联分析结果 ID',
  `action`      varchar(32)  NOT NULL                COMMENT 'config_update/connection_test/trigger/success/failed/blocked/skipped/feedback',
  `operator`    varchar(64)  NOT NULL DEFAULT 'system' COMMENT '触发者（用户名或 system）',
  `model`       varchar(128) NOT NULL DEFAULT ''     COMMENT '模型名',
  `latency_ms`  int          NOT NULL DEFAULT 0      COMMENT '耗时（毫秒）',
  `blocked`     tinyint(1)   NOT NULL DEFAULT 0      COMMENT '是否命中内容过滤',
  `ok`          tinyint(1)   NOT NULL DEFAULT 1      COMMENT '动作是否成功',
  `detail`      json                                  COMMENT '动作详情（不含密钥明文）',
  `created_at`  datetime     NOT NULL                COMMENT '创建时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_ai_audit_logs_action` (`action`),
  KEY `ix_ai_audit_logs_created_at` (`created_at`),
  KEY `ix_ai_audit_logs_anomaly_id` (`anomaly_id`),
  KEY `ix_ai_audit_logs_analysis_id` (`analysis_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI 操作审计日志';
