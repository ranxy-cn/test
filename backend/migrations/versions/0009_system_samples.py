"""system metric samples: local host realtime sampling

新增本机系统资源实时采样表（system_metric_samples）：API 进程启动后每 5 秒
采集本机 CPU/内存/磁盘/负载/网速并落库（不回填历史），保留 7 天。

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    existing_tables = set(sa.inspect(bind).get_table_names())
    if "system_metric_samples" not in existing_tables:
        op.create_table(
            "system_metric_samples",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
            sa.Column("ts", sa.DateTime(), nullable=False, comment="采样时间（UTC）"),
            sa.Column("cpu", sa.Float(), nullable=True, comment="CPU 使用率 %"),
            sa.Column("mem", sa.Float(), nullable=True, comment="内存使用率 %"),
            sa.Column("disk", sa.Float(), nullable=True, comment="磁盘使用率 %"),
            sa.Column("load1", sa.Float(), nullable=True, comment="1 分钟负载"),
            sa.Column("net_rx_bps", sa.Float(), nullable=True, comment="下载速率（字节/秒）"),
            sa.Column("net_tx_bps", sa.Float(), nullable=True, comment="上传速率（字节/秒）"),
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                server_default="real",
                comment="来源：real /proc 读取",
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False, comment="入库时间（UTC）"),
            sa.PrimaryKeyConstraint("id"),
            comment="本机系统资源实时采样（5 秒粒度，启动后开始记录）",
        )
        op.create_index("ix_system_metric_samples_ts", "system_metric_samples", ["ts"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("system_metric_samples")
