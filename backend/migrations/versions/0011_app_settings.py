"""agent architecture: app settings kv

新增 app_settings 键值表：存放系统级配置（当前用于子机 Agent 全局默认
采集/推送配置）。无该行时使用代码内置 DEFAULT_AGENT_CONFIG。

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        comment="系统级键值配置",
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("app_settings")
