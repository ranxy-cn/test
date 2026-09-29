-- =============================================================================
-- DevOpsAgent DDL 变更脚本
-- =============================================================================
-- 版本：0004（对应迁移 versions/0004_dict_entries.py）
-- 目的：业务字典表（dict_entries）——告警标题 / 资产 / 预案等业务编码 → 中文名映射，
--       供任务单列表等页面做中文枚举展示。
-- 约定：种子数据由迁移通过 app/dict_seed.py 写入（只补缺失项，不覆盖已有数据）；
--       后续每次表结构变更必须在本目录新增对应编号的 DDL 文件并同步迁移脚本。
-- =============================================================================

SET NAMES utf8mb4;

-- =============================================================================
-- 一、新表
-- =============================================================================

-- 业务字典（枚举中文名映射）
CREATE TABLE IF NOT EXISTS `dict_entries` (
  `id`         int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `dict_type`  varchar(32)  NOT NULL                COMMENT '字典类型：trigger 告警标题 / asset 资产 / action 预案',
  `code`       varchar(128) NOT NULL                COMMENT '业务编码（原值，如 CPU usage > 85% for 5 minutes）',
  `label`      varchar(128) NOT NULL                COMMENT '中文展示名',
  `sort_order` int          NOT NULL DEFAULT '0'    COMMENT '排序值（小的在前）',
  `status`     varchar(16)  NOT NULL DEFAULT 'enabled' COMMENT '状态：enabled 启用 / disabled 停用',
  `remark`     varchar(256) NOT NULL DEFAULT ''    COMMENT '备注',
  `created_at` datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at` datetime     NOT NULL                COMMENT '更新时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_dict_type_code` (`dict_type`,`code`),
  KEY `ix_dict_entries_dict_type` (`dict_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='业务字典（枚举中文名映射）';

-- =============================================================================
-- 二、种子数据（迁移通过 app/dict_seed.py 自动写入，此处仅为人工初始化参考）
-- =============================================================================
-- 告警标题（trigger）：
--   CPU usage > 85% for 5 minutes      → CPU 使用率持续超 85%（5 分钟）
--   CPU usage too high                 → CPU 使用率过高
--   MySQL replication lag too high     → MySQL 主从复制延迟过高
--   mystery native crash               → 未知原因进程崩溃
-- 资产（asset）：
--   ast-order-app-01 ~ 03              → 订单系统-应用01 ~ 03
--   ast-order-db-01                    → 订单系统-数据库01
--   ast-order-gw-01                    → 订单系统-网关01
--   ast-order-job-01                   → 订单系统-作业01
--   ast-order-lb-01                    → 订单系统-负载均衡01
--   ast-order-redis-01                 → 订单系统-缓存01
--   ast-order-unreachable              → 订单系统-失联节点
--   ast-zabbix-server                  → Zabbix 监控服务器
-- 预案（action）：
--   ACT-ROLLING-RESTART                → 滚动重启服务
--   ACT-DB-FAILOVER                    → 数据库主从切换
--   ACT-CLEAN-TMPLOG                   → 清理临时日志
--   ACT-RESTART-PROBE                  → 重启服务并探活
-- =============================================================================
