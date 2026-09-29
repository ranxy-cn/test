"""asset group field

Revision ID: 7c1a4f2b9d03
Revises: 9962e08657c7
Create Date: 2026-09-22 11:40:00.000000

资产业务分组：assets.group（如 订单系统 / 财务系统），用于母机下子机的业务归组展示。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "7c1a4f2b9d03"
down_revision = "9962e08657c7"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    if not _column_exists("assets", "group"):
        op.add_column("assets", sa.Column("group", sa.String(length=64), nullable=False, server_default=""))


def downgrade() -> None:
    if _column_exists("assets", "group"):
        op.drop_column("assets", "group")
