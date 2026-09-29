-- =============================================================================
-- DevOpsAgent DDL 变更脚本
-- =============================================================================
-- 版本：0003（对应迁移 versions/0003_dynamic_menus.py）
-- 目的：动态菜单权限系统——新增菜单/按钮资源树（menus）与角色授权表（role_menus），
--       新增权限点 roles:manage、menus:manage，初始化菜单树种子与内置角色授权。
-- 约定：种子数据由迁移通过 app/menus_seed.py 写入（与本文件种子部分保持一致）；
--       后续每次表结构变更必须在本目录新增对应编号的 DDL 文件并同步迁移脚本。
-- =============================================================================

SET NAMES utf8mb4;

-- =============================================================================
-- 一、新表
-- =============================================================================

-- 菜单/路由/按钮资源树（type=dir 目录；type=menu 页面菜单；type=button 页面按钮）
CREATE TABLE IF NOT EXISTS `menus` (
  `id`         int          NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `parent_id`  int          DEFAULT NULL            COMMENT '父菜单 ID（空=顶级，fk → menus.id，级联删除）',
  `code`       varchar(64)  NOT NULL                COMMENT '资源唯一编码，如 menu:tickets / btn:ticket-approve',
  `name`       varchar(64)  NOT NULL                COMMENT '显示名称',
  `type`       varchar(16)  NOT NULL                COMMENT '类型：dir 目录 / menu 菜单 / button 按钮',
  `path`       varchar(128) DEFAULT NULL            COMMENT '前端路由路径（menu 型必填）',
  `perm_code`  varchar(64)  DEFAULT NULL            COMMENT '所需权限点编码（关联 permissions.code）',
  `icon`       varchar(64)  NOT NULL                COMMENT '图标名称（前端图标映射）',
  `sort_order` int          NOT NULL                COMMENT '排序值（小的在前）',
  `visible`    tinyint(1)   NOT NULL                COMMENT '是否在侧边栏显示：1 显示 / 0 隐藏',
  `status`     varchar(16)  NOT NULL                COMMENT '状态：enabled 启用 / disabled 停用',
  `remark`     varchar(256) NOT NULL                COMMENT '备注',
  `created_at` datetime     NOT NULL                COMMENT '创建时间（UTC）',
  `updated_at` datetime     NOT NULL                COMMENT '更新时间（UTC）',
  PRIMARY KEY (`id`),
  UNIQUE KEY `ix_menus_code` (`code`),
  CONSTRAINT `menus_ibfk_1` FOREIGN KEY (`parent_id`) REFERENCES `menus` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='菜单/路由/按钮资源树';

-- 角色-菜单授权关联（多对多）
CREATE TABLE IF NOT EXISTS `role_menus` (
  `role_id` int NOT NULL COMMENT '角色 ID（联合主键，fk → roles.id，级联删除）',
  `menu_id` int NOT NULL COMMENT '菜单 ID（联合主键，fk → menus.id，级联删除）',
  PRIMARY KEY (`role_id`,`menu_id`),
  CONSTRAINT `role_menus_ibfk_1` FOREIGN KEY (`menu_id`) REFERENCES `menus` (`id`) ON DELETE CASCADE,
  CONSTRAINT `role_menus_ibfk_2` FOREIGN KEY (`role_id`) REFERENCES `roles` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色-菜单授权关联';

-- =============================================================================
-- 二、种子数据（迁移通过 app/menus_seed.py 写入，以下为等效说明与示例）
-- =============================================================================

-- 新增权限点（幂等）
INSERT INTO `permissions` (`code`, `name`, `description`, `created_at`)
SELECT 'roles:manage', '角色权限管理', '角色增删、菜单授权与权限点分配', UTC_TIMESTAMP()
WHERE NOT EXISTS (SELECT 1 FROM `permissions` WHERE `code` = 'roles:manage');

INSERT INTO `permissions` (`code`, `name`, `description`, `created_at`)
SELECT 'menus:manage', '菜单管理', '菜单/路由/按钮资源树的维护', UTC_TIMESTAMP()
WHERE NOT EXISTS (SELECT 1 FROM `permissions` WHERE `code` = 'menus:manage');

-- 菜单树种子（共 20 个节点；实际写入由迁移按 app/menus_seed.py MENU_TREE 执行）：
--   dir   menu:system                              系统管理
--   menu  menu:tickets    /tickets  tickets:read   任务单
--     btn btn:ticket-approve  tickets:operate       审批通过/驳回
--     btn btn:ticket-retry    tickets:operate       重试执行
--     btn btn:ticket-webhook  tickets:operate       模拟告警接入
--   menu  menu:employee   /employee catalog:read   数字员工
--   menu  menu:assets     /assets    assets:read   资产台账
--   menu  menu:backups    /backups   backups:read  备份管理
--     btn btn:backup-run    backups:operate         立即备份
--     btn btn:backup-verify backups:operate         恢复演练
--   menu  menu:notifications /notifications notifications:read 通知中心
--     btn btn:notification-read notifications:read   标记已读
--   menu  menu:report     /report    reports:read  运维日报
--   menu  menu:status     /status    status:read   集成状态
--     btn btn:stress-start  tools:operate           启动压测
--     btn btn:stress-stop   tools:operate           停止压测
--   menu  menu:users      /users     users:manage  用户管理
--   menu  menu:roles      /roles     roles:manage  角色权限
--   menu  menu:menus      /menus     menus:manage  菜单管理
--
-- 角色初始授权（role_menus）：
--   admin    → 全部节点（且接口禁止削减，保证最高管理员满权限）
--   operator → 全部业务菜单及按钮（不含系统管理目录及其子菜单）
--   viewer   → 仅业务菜单（dir/menu，不含任何 button 节点）
