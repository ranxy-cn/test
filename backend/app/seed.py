from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Asset, DigitalEmployee, MaintenanceWindow, Permission, Role, RolePermission, User, UserRole, utcnow
from app.security import hash_password
from app.services.backups import seed_backup_jobs

# 权限点目录：模块:动作
PERMISSIONS: list[tuple[str, str, str]] = [
    ("anomalies:read", "异常告警查看", "查看异常/恢复条目列表"),
    ("tickets:read", "任务单查看", "查看任务单列表与详情"),
    ("tickets:operate", "任务单操作", "审批、驳回、重试执行"),
    ("catalog:read", "预案目录查看", "查看预案目录与数字员工档案"),
    ("assets:read", "资产查看", "查看资产台账"),
    ("assets:write", "资产登记", "登记/更新资产（自动纳管回写）"),
    ("backups:read", "备份查看", "查看备份任务与运行记录"),
    ("backups:operate", "备份操作", "触发备份与恢复演练"),
    ("notifications:read", "通知查看", "查看与标记通知"),
    ("reports:read", "日报查看", "查看运维日报"),
    ("audit:read", "审计查看", "查看操作审计链"),
    ("status:read", "集成状态查看", "查看集成与适配层状态"),
    ("tools:read", "工具状态查看", "查看压测等工具状态"),
    ("tools:operate", "工具操作", "启动/停止压测等工具"),
    ("users:manage", "用户管理", "用户、角色与权限管理"),
    ("roles:manage", "角色权限管理", "角色增删、菜单授权与权限点分配"),
    ("menus:manage", "菜单管理", "菜单/路由/按钮资源树的维护"),
]

READ_PERMS = [c for c, _, _ in PERMISSIONS if c.endswith(":read")]
OPERATE_PERMS = [c for c, _, _ in PERMISSIONS if c.endswith(":operate")]

ROLES: list[tuple[str, str, str, list[str]]] = [
    ("viewer", "只读用户", "仅查看各类页面", READ_PERMS),
    ("operator", "运维操作员", "查看 + 审批/工具/备份操作", READ_PERMS + OPERATE_PERMS),
    ("admin", "管理员", "全部权限，含用户管理", [c for c, _, _ in PERMISSIONS]),
]


def seed_rbac(db: Session) -> None:
    perm_by_code = {p.code: p for p in db.scalars(select(Permission)).all()}
    for code, name, desc in PERMISSIONS:
        if code not in perm_by_code:
            perm_by_code[code] = Permission(code=code, name=name, description=desc)
            db.add(perm_by_code[code])
    db.flush()

    role_by_code = {r.code: r for r in db.scalars(select(Role)).all()}
    for code, name, desc, perms in ROLES:
        role = role_by_code.get(code)
        if role is None:
            role = Role(code=code, name=name, description=desc)
            db.add(role)
            db.flush()
            role_by_code[code] = role
        existing = {rp.permission_id for rp in db.scalars(
            select(RolePermission).where(RolePermission.role_id == role.id)
        ).all()}
        for pc in perms:
            pid = perm_by_code[pc].id
            if pid not in existing:
                db.add(RolePermission(role_id=role.id, permission_id=pid))
    db.flush()

    # 菜单/按钮资源树 + 角色菜单授权（仅当角色尚无任何菜单授权时初始化，
    # 避免每次重启覆盖管理员在「角色权限」页的自定义分配）
    from app.menus_seed import grant_tree_to_role, role_default_menu_codes, seed_menus
    from app.models import RoleMenu

    menus = seed_menus(db)
    for code, name, _desc, _perms in ROLES:
        role = role_by_code[code]
        has_grants = (
            db.scalar(select(RoleMenu.role_id).where(RoleMenu.role_id == role.id).limit(1))
            is not None
        )
        if not has_grants:
            grant_tree_to_role(db, role.id, menus, role_default_menu_codes(code))
    db.flush()

    # 业务字典（告警标题/资产/预案中文名映射，只补缺失项）
    from app.dict_seed import seed_dict

    seed_dict(db)
    db.flush()

    # 初始管理员（仅当无任何用户时创建一次）
    if db.scalar(select(User.id).limit(1)) is None:
        settings = get_settings()
        admin = User(
            username="admin",
            password_hash=hash_password(settings.admin_initial_password),
            display_name="管理员",
            is_active=True,
        )
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=role_by_code["admin"].id))


def seed_if_empty(db: Session) -> None:
    seed_rbac(db)

    if db.get(DigitalEmployee, "DE-OPS-001") is None:
        db.add(
            DigitalEmployee(
                id="DE-OPS-001",
                name="运维数字员工·小维",
                team="平台运维组",
                systems=["订单系统"],
                manager="张三",
                oncall="李四",
                skill_version="skill-v1.0.0",
                auth_expires_at="2027-12-31",
                status="active",
                duties=[
                    "日常巡检 → 健康清单 + 异常证据",
                    "异常处置 → 影响分析 + 恢复验证",
                    "备份管理 → 备份结果 + 恢复点（二期）",
                    "恢复协助 → 恢复步骤 + 校验结果",
                    "工作汇报 → 日报 + 未结事项",
                ],
            )
        )

    # 演示数据：仅当开关开启 **且资产表为空** 时才种，避免污染真实台账
    if get_settings().seed_demo_assets and db.query(Asset).first() is None:
        assets: list[Asset] = [
            Asset(
            id="ast-order-app-01",
            hostname="order-app-01",
            zabbix_host="order-app-01",
            external_id="10001",
            app="订单系统",
            role="app",
            owner="张三",
            tenant_id="tenant-default",
            reachable=True,
            db_ok=True,
        ),
        Asset(
            id="ast-order-app-02",
            hostname="order-app-02",
            zabbix_host="order-app-02",
            external_id="10002",
            app="订单系统",
            role="app",
            owner="张三",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-app-03",
            hostname="order-app-03",
            zabbix_host="order-app-03",
            external_id="10003",
            app="订单系统",
            role="app",
            owner="张三",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-gw-01",
            hostname="order-gateway-01",
            zabbix_host="order-gateway-01",
            external_id="10011",
            app="订单系统",
            role="gateway",
            owner="李四",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-db-01",
            hostname="order-db-01",
            zabbix_host="order-db-01",
            external_id="10021",
            app="订单系统",
            role="mysql",
            owner="王五",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-redis-01",
            hostname="order-redis-01",
            zabbix_host="order-redis-01",
            external_id="10031",
            app="订单系统",
            role="redis",
            owner="李四",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-lb-01",
            hostname="order-lb-01",
            zabbix_host="order-lb-01",
            external_id="10041",
            app="订单系统",
            role="lb",
            owner="李四",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-job-01",
            hostname="order-job-01",
            zabbix_host="order-job-01",
            external_id="10051",
            app="订单系统",
            role="job",
            owner="张三",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-zabbix-server",
            hostname="Zabbix server",
            zabbix_host="Zabbix server",
            external_id="10084",
            app="监控",
            role="monitor",
            owner="张三",
            tenant_id="tenant-default",
        ),
        Asset(
            id="ast-order-unreachable",
            hostname="order-app-down",
            zabbix_host="order-app-down",
            external_id="",
            app="订单系统",
            role="app",
            owner="张三",
            tenant_id="tenant-default",
            reachable=False,
        ),
        ]
        for asset in assets:
            if db.get(Asset, asset.id) is None:
                db.add(asset)
        existing_mw = db.scalar(select(MaintenanceWindow).limit(1))
        if existing_mw is None:
            now = utcnow()
            db.add(
                MaintenanceWindow(
                    asset_id="ast-order-app-03",
                    reason="计划变更窗口：扩容演练",
                    starts_at=now - timedelta(hours=1),
                    ends_at=now + timedelta(days=1),
                )
            )
        seed_backup_jobs(db)
    for row in db.scalars(select(Asset)).all():
        if not row.zabbix_host:
            row.zabbix_host = row.hostname
    db.flush()
