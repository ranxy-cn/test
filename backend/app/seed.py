from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, DigitalEmployee, MaintenanceWindow, utcnow
from app.services.backups import seed_backup_jobs


def seed_if_empty(db: Session) -> None:
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

    assets = [
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
    for row in db.scalars(select(Asset)).all():
        if not row.zabbix_host:
            row.zabbix_host = row.hostname

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
    db.flush()
    seed_backup_jobs(db)
