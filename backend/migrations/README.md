# 数据库迭代历史（Database Migration History）

本项目数据库为 **MySQL 8.0**，使用 **Alembic** 管理全部表结构的创建与演进。
任何表结构变更都必须以迁移脚本的形式进入本目录，禁止手工在库上改表。

## 迭代历史

| 版本 | 文件 | 日期 | 内容 |
| --- | --- | --- | --- |
| 0001 | [0001_init_schema.py](versions/0001_init_schema.py) | 2026-09-20 | 初始 schema：20 张表（13 张业务表 + 7 张 RBAC 鉴权表） |

## 0001 初始表结构一览

### 业务表（13 张）
| 表名 | 说明 | 关键字段 |
| --- | --- | --- |
| `digital_employees` | 数字员工档案 | id（DE-OPS-001）, systems/duties JSON |
| `assets` | 主机资产 | hostname 唯一, zabbix_host, external_id, tenant_id |
| `maintenance_windows` | 维护窗口 | asset_id → assets.id, starts_at/ends_at |
| `tickets` | 工单 | number/idempotency_key 唯一, status, asset_id → assets.id |
| `ticket_events` | 工单事件流 | ticket_id → tickets.id |
| `approvals` | 人工审批单 | ticket_id → tickets.id, status, approver |
| `audit_logs` | 操作审计链 | event_type, actor, evidence_refs JSON |
| `alert_events` | 告警事件 | idempotency_key 唯一约束, ticket_id |
| `resource_locks` | 资产操作锁 | asset_id 主键, expires_at |
| `action_failures` | 动作失败熔断 | (asset_id, action_id) 复合主键 |
| `notifications` | 站内通知 | kind, read |
| `backup_jobs` | 备份任务 | id, schedule, last_backup_ok |
| `backup_runs` | 备份运行记录 | job_id → backup_jobs.id |

### RBAC 鉴权表（7 张）
| 表名 | 说明 | 关键字段 |
| --- | --- | --- |
| `users` | 用户 | username 唯一, password_hash(bcrypt), is_active, failed_attempts, locked_until |
| `roles` | 角色 | code 唯一（admin/operator/viewer） |
| `permissions` | 权限点 | code 唯一，形如 `tickets:operate`（共 13 项） |
| `user_roles` | 用户-角色 多对多 | 复合主键 (user_id, role_id)，ON DELETE CASCADE |
| `role_permissions` | 角色-权限 多对多 | 复合主键 (role_id, permission_id)，ON DELETE CASCADE |
| `user_tokens` | JWT jti 白名单 | jti 主键, expires_at, revoked_at（登出/改密即吊销） |
| `login_logs` | 登录审计 | username, success, fail_reason, ip, user_agent |

## 约定

- 所有时间字段使用 `TZDateTime`（应用层统一 UTC 存取，库内 DATETIME 无时区）。
- 主键统一 `BIGINT autoincrement`；外键显式命名 `fk_<table>_<refcol>`，ON DELETE CASCADE/RESTRICT 视语义而定。
- 常用查询列建索引，命名 `ix_<table>_<col>`。

## 如何新增一次迭代

```bash
cd backend
# 1. 修改 app/models.py 中的模型
# 2. 自动生成迁移脚本（对比当前库结构）
alembic revision --autogenerate -m "add xxx"
# 3. 检查并手工修正生成的脚本，把随机 revision id 改成语义化编号（0002、0003...）
# 4. 在本地 sqlite 或测试库上验证
alembic upgrade head
# 5. 提交到 git，并在上表「迭代历史」中登记一行
```

## 连接配置

- 连接串由环境变量 `DATABASE_URL` 提供，格式：
  `mysql+pymysql://<user>:<password>@<host>:3306/<db>?charset=utf8mb4`
- 生产库：`devops_agent`（专用账号 `devops_app`，不使用 root 跑应用）。
- API 容器启动命令中先执行 `alembic upgrade head` 再启动 uvicorn，保证表结构随版本演进。
