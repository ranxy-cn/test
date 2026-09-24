"""anomaly diagnostics columns

异常条目新增诊断字段：上机采集的进程/PID/日志等诊断数据、采集状态、写死排查指南由服务层按类别生成。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    existing_cols = {c["name"] for c in sa.inspect(bind).get_columns("anomaly_events")}
    if "diagnostics" not in existing_cols:
        op.add_column(
            "anomaly_events",
            sa.Column("diagnostics", sa.JSON(), nullable=True, comment="上机诊断数据（进程/PID/日志等命令输出）"),
        )
    if "diag_status" not in existing_cols:
        op.add_column(
            "anomaly_events",
            sa.Column(
                "diag_status",
                sa.String(length=16),
                nullable=False,
                server_default="none",
                comment="采集状态：none 未采集 / running 采集中 / done 完成 / failed 失败",
            ),
        )
    if "diag_at" not in existing_cols:
        op.add_column(
            "anomaly_events",
            sa.Column("diag_at", sa.DateTime(), nullable=True, comment="最近一次采集时间（UTC）"),
        )
    if "diag_error" not in existing_cols:
        op.add_column(
            "anomaly_events",
            sa.Column("diag_error", sa.String(length=512), nullable=False, server_default="", comment="最近一次采集失败原因"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_column("anomaly_events", "diag_error")
    op.drop_column("anomaly_events", "diag_at")
    op.drop_column("anomaly_events", "diag_status")
    op.drop_column("anomaly_events", "diagnostics")