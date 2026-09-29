-- 0007: 本机系统资源实时采样表（CPU/内存/磁盘/负载/网速，5 秒粒度）
-- 与 app/models.py SystemMetricSample / alembic 0009 保持一致；可手工执行（IF NOT EXISTS 幂等）。

CREATE TABLE IF NOT EXISTS `system_metric_samples` (
  `id`         int         NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `ts`         datetime    NOT NULL                COMMENT '采样时间（UTC）',
  `cpu`        double      DEFAULT NULL            COMMENT 'CPU 使用率 %',
  `mem`        double      DEFAULT NULL            COMMENT '内存使用率 %',
  `disk`       double      DEFAULT NULL            COMMENT '磁盘使用率 %',
  `load1`      double      DEFAULT NULL            COMMENT '1 分钟负载',
  `net_rx_bps` double      DEFAULT NULL            COMMENT '下载速率（字节/秒）',
  `net_tx_bps` double      DEFAULT NULL            COMMENT '上传速率（字节/秒）',
  `source`     varchar(16) NOT NULL DEFAULT 'real' COMMENT '来源：real /proc 读取',
  `created_at` datetime    NOT NULL                COMMENT '入库时间（UTC）',
  PRIMARY KEY (`id`),
  KEY `ix_system_metric_samples_ts` (`ts`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='本机系统资源实时采样（5 秒粒度，启动后开始记录）';
