"""anomaly logs: raw webhook notification records

新增异常告警原始通知日志表（anomaly_logs）：每条 Zabbix webhook 推送（异常/恢复）都留痕，
保留完整的原始载荷，避免恢复通知覆盖异常时的原始记录。半夜短时异常也可完整回溯。

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    existing_tables = set(sa.inspect(bind).get_table_names())
    if "anomaly_logs" not in existing_tables:
        op.create_table(
            "anomaly_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
            sa.Column("anomaly_id", sa.Integer(), nullable=False, comment="关联 anomaly_events.id"),
            sa.Column("event_id", sa.String(length=128), nullable=False, server_default="", comment="Zabbix 事件 ID"),
            sa.Column(
                "action",
                sa.String(length=16),
                nullable=False,
                server_default="problem",
                comment="通知类型：problem 异常通知 / recovered 恢复通知",
            ),
            sa.Column("payload", sa.JSON(), nullable=True, comment="原始 webhook 载荷"),
            sa.Column("received_at", sa.DateTime(), nullable=False, comment="收到通知时间（UTC）"),
            sa.PrimaryKeyConstraint("id"),
            comment="异常告警原始通知日志（每条 webhook 留痕）",
        )
        op.create_index(op.f("ix_anomaly_logs_anomaly_id"), "anomaly_logs", ["anomaly_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_index(op.f("ix_anomaly_logs_anomaly_id"), table_name="anomaly_logs")
    op.drop_table("anomaly_logs")
