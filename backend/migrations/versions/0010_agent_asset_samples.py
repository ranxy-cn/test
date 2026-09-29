"""agent architecture: asset-dimension samples

system_metric_samples 增加 asset_id 列：NULL 表示母机本机采样（/proc），
非空表示对应子机由自研 Agent 上报，实现多资产数据隔离。

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    cols = {c["name"] for c in sa.inspect(bind).get_columns("system_metric_samples")}
    if "asset_id" not in cols:
        op.add_column(
            "system_metric_samples",
            sa.Column("asset_id", sa.String(length=64), nullable=True, comment="资产 ID（NULL=母机本机采样）"),
        )
    op.create_index(
        "ix_system_metric_samples_asset_ts",
        "system_metric_samples",
        ["asset_id", "ts"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_index("ix_system_metric_samples_asset_ts", table_name="system_metric_samples")
    op.drop_column("system_metric_samples", "asset_id")
