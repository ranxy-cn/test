-- 0016: 负责人通知联系人（owner_contacts）
-- 与 app/models.py OwnerContact、alembic 0016 保持一致；可手工执行（重复执行会报表已存在）。

CREATE TABLE IF NOT EXISTS `owner_contacts` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL COMMENT '负责人姓名（与 assets.owner 对应）',
  `phone` varchar(32) NOT NULL DEFAULT '' COMMENT '电话/短信接收号码',
  `email` varchar(128) NOT NULL DEFAULT '',
  `dingtalk_webhook` varchar(512) NOT NULL DEFAULT '' COMMENT '钉钉自定义机器人 webhook',
  `feishu_webhook` varchar(512) NOT NULL DEFAULT '' COMMENT '飞书自定义机器人 webhook',
  `wecom_webhook` varchar(512) NOT NULL DEFAULT '' COMMENT '企业微信群机器人 webhook',
  `channels` json NOT NULL COMMENT '启用的推送渠道列表',
  `updated_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_owner_contacts_name` (`name`),
  KEY `ix_owner_contacts_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='负责人通知联系人（告警推送渠道配置）';
