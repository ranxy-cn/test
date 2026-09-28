"""metric samples: periodic CPU/mem/disk/load storage

新增监控指标采样表（metric_samples）：celery beat 每 5 分钟采集全部资产的
CPU/内存/磁盘/负载并落库，支撑 24 小时趋势、多日对比、历史基线、异常检测与预测。

Revision ID: 0008
Revises: b7d2f9a4c1e8
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "b7d2f9a4c1e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    existing_tables = set(sa.inspect(bind).get_table_names())
    if "metric_samples" not in existing_tables:
        op.create_table(
            "metric_samples",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
            sa.Column("asset_id", sa.String(length=64), nullable=False, comment="资产 ID"),
            sa.Column("ts", sa.DateTime(), nullable=False, comment="采样时间（UTC）"),
            sa.Column("cpu", sa.Float(), nullable=True, comment="CPU 使用率 %"),
            sa.Column("mem", sa.Float(), nullable=True, comment="内存使用率 %"),
            sa.Column("disk", sa.Float(), nullable=True, comment="磁盘使用率 %"),
            sa.Column("load1", sa.Float(), nullable=True, comment="1 分钟负载"),
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default="real",
                comment="来源：real Zabbix / mock 演示",
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False, comment="入库时间（UTC）"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("asset_id", "ts", name="uq_metric_asset_ts"),
            comment="监控指标采样（5 分钟粒度落库）",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("metric_samples")
