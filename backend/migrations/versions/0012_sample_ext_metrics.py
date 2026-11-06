"""alert policy v2: system_metric_samples.ext

告警策略 v2 配套：system_metric_samples 新增 ext JSON 列，存放 agent v1.1+
扩展采集指标（swap/inode/磁盘IO await/TCP 状态/丢包延迟/带宽占比/OOM 事件/
进程与端口状态/Prometheus 抓取指标），供告警引擎做规则判定。

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.add_column("system_metric_samples", sa.Column("ext", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_column("system_metric_samples", "ext")
