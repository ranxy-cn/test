"""anomaly events: simple abnormal/recovered entries

新增异常告警条目表（anomaly_events）：Zabbix webhook 只记「异常/恢复」两态条目。
补异常告警权限点（anomalies:read）与「异常告警」菜单节点，并为已有授权角色补授权。

Revision ID: 0005
Revises: a3f8c2d1e5b7
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0005"
down_revision = "a3f8c2d1e5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # 本地/测试 sqlite 不跑迁移（create_all + seed_if_empty 覆盖）
        return

    # 幂等：表可能已按 ddl/0005_anomaly_events.sql 手工建过
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "anomaly_events" not in existing_tables:
        op.create_table(
            "anomaly_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
            sa.Column("event_id", sa.String(length=128), nullable=False, comment="Zabbix 事件 ID（幂等键）"),
            sa.Column("hostid", sa.String(length=32), nullable=False, server_default="", comment="Zabbix 主机 ID"),
            sa.Column("host", sa.String(length=128), nullable=False, server_default="", comment="Zabbix 技术主机名"),
            sa.Column("hostname", sa.String(length=128), nullable=False, server_default="", comment="Zabbix 可见名称"),
            sa.Column("ip", sa.String(length=64), nullable=False, server_default="", comment="主机 IP"),
            sa.Column("trigger_name", sa.String(length=256), nullable=False, server_default="", comment="触发器名称"),
            sa.Column("severity", sa.String(length=32), nullable=False, server_default="high", comment="严重级别"),
            sa.Column("message", sa.String(length=256), nullable=False, server_default="", comment="告警消息"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="abnormal", comment="状态：abnormal 异常 / recovered 恢复"),
            sa.Column("asset_id", sa.String(length=64), nullable=True, comment="关联资产 ID（可空，资产删除不受影响）"),
            sa.Column("payload", sa.JSON(), nullable=True, comment="原始 webhook 载荷"),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False, comment="首次异常时间（UTC）"),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, comment="最近一次通知时间（UTC）"),
            sa.Column("recovered_at", sa.DateTime(), nullable=True, comment="恢复时间（UTC）"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_anomaly_event_id"),
            comment="异常告警条目（异常/恢复两态）",
        )
        op.create_index(op.f("ix_anomaly_events_event_id"), "anomaly_events", ["event_id"], unique=True)
        op.create_index(op.f("ix_anomaly_events_status"), "anomaly_events", ["status"], unique=False)

    # ===== 种子：权限点 + 菜单节点 + 已授权角色补授权 =====
    from app.menus_seed import seed_menus
    from app.models import Permission, RoleMenu

    db = Session(bind=bind)
    try:
        if db.scalar(sa.select(Permission.id).where(Permission.code == "anomalies:read")) is None:
            db.add(Permission(code="anomalies:read", name="异常告警查看", description="查看异常/恢复条目列表"))
        db.flush()

        menus = seed_menus(db)
        menu = menus.get("menu:anomalies")
        if menu is not None:
            # 已有任何菜单授权的角色（生产存量角色）补授「异常告警」，避免上线后看不到新页
            role_ids = {rm.role_id for rm in db.scalars(sa.select(RoleMenu)).all()}
            for role_id in role_ids:
                exists = db.scalar(
                    sa.select(RoleMenu.menu_id).where(
                        RoleMenu.role_id == role_id, RoleMenu.menu_id == menu.id
                    )
                )
                if exists is None:
                    db.add(RoleMenu(role_id=role_id, menu_id=menu.id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_index(op.f("ix_anomaly_events_status"), table_name="anomaly_events")
    op.drop_index(op.f("ix_anomaly_events_event_id"), table_name="anomaly_events")
    op.drop_table("anomaly_events")
    op.execute(
        "DELETE FROM role_menus WHERE menu_id IN (SELECT id FROM menus WHERE code = 'menu:anomalies')"
    )
    op.execute("DELETE FROM menus WHERE code = 'menu:anomalies'")
    op.execute("DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'anomalies:read')")
    op.execute("DELETE FROM permissions WHERE code = 'anomalies:read'")
