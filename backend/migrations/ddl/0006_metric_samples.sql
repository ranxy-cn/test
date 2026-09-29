-- 0006: 监控指标采样表（CPU/内存/磁盘/负载定时落库）
-- 与 app/models.py MetricSample / alembic 0008 保持一致；可手工执行（IF NOT EXISTS 幂等）。

CREATE TABLE IF NOT EXISTS `metric_samples` (
  `id`         int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `asset_id`   varchar(64) NOT NULL                COMMENT '资产 ID',
  `ts`         datetime    NOT NULL                COMMENT '采样时间（UTC）',
  `cpu`        double      DEFAULT NULL            COMMENT 'CPU 使用率 %',
  `mem`        double      DEFAULT NULL            COMMENT '内存使用率 %',
  `disk`       double      DEFAULT NULL            COMMENT '磁盘使用率 %',
  `load1`      double      DEFAULT NULL            COMMENT '1 分钟负载',
  `source`     varchar(16) NOT NULL DEFAULT 'real' COMMENT '来源：real Zabbix / mock 演示',
  `created_at` datetime    NOT NULL                COMMENT '入库时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_metric_asset_ts` (`asset_id`, `ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='监控指标采样（5 分钟粒度落库）';
