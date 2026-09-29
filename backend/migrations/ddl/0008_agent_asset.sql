-- 0010: 子机 Agent 采集体系 —— system_metric_samples 增加资产维度
-- asset_id 为空表示母机本机（/proc）采样；非空为子机 Agent 上报数据。

ALTER TABLE system_metric_samples
    ADD COLUMN asset_id VARCHAR(64) NULL COMMENT '资产 ID（NULL=母机本机采样）';

CREATE INDEX ix_system_metric_samples_asset_ts ON system_metric_samples (asset_id, ts);
