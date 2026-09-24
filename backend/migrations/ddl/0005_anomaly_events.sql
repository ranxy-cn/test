-- 0005: 异常告警条目表（异常/恢复两态）
-- 与 app/models.py AnomalyEvent / alembic 0005 保持一致；可手工执行（幂等需自行判断表存在）。

CREATE TABLE IF NOT EXISTS `anomaly_events` (
  `id`            int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `event_id`      varchar(128) NOT NULL                COMMENT 'Zabbix 事件 ID（幂等键）',
  `hostid`        varchar(32)  NOT NULL DEFAULT ''     COMMENT 'Zabbix 主机 ID',
  `host`          varchar(128) NOT NULL DEFAULT ''     COMMENT 'Zabbix 技术主机名',
  `hostname`      varchar(128) NOT NULL DEFAULT ''     COMMENT 'Zabbix 可见名称',
  `ip`            varchar(64)  NOT NULL DEFAULT ''     COMMENT '主机 IP',
  `trigger_name`  varchar(256) NOT NULL DEFAULT ''     COMMENT '触发器名称',
  `severity`      varchar(32)  NOT NULL DEFAULT 'high' COMMENT '严重级别',
  `message`       varchar(256) NOT NULL DEFAULT ''     COMMENT '告警消息',
  `status`        varchar(16)  NOT NULL DEFAULT 'abnormal' COMMENT '状态：abnormal 异常 / recovered 恢复',
  `asset_id`      varchar(64)  DEFAULT NULL            COMMENT '关联资产 ID（可空）',
  `payload`       json         DEFAULT NULL            COMMENT '原始 webhook 载荷',
  `first_seen_at` datetime     NOT NULL                COMMENT '首次异常时间（UTC）',
  `last_seen_at`  datetime     NOT NULL                COMMENT '最近一次通知时间（UTC）',
  `recovered_at`  datetime     DEFAULT NULL            COMMENT '恢复时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_anomaly_event_id` (`event_id`),
  KEY `ix_anomaly_events_event_id` (`event_id`),
  KEY `ix_anomaly_events_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='异常告警条目（异常/恢复两态）';
