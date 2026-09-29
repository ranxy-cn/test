"""asset probe fields

Revision ID: 9962e08657c7
Revises: 0004
Create Date: 2026-09-22 09:52:57.919102

资产探活字段：last_seen_at / last_check_at / unreachable_reason。
注：生产库 assets 表已有数据，NOT NULL 列必须带 server_default。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "9962e08657c7"
down_revision = "0004"
branch_labels = None
depends_on = None

TZ = "app.database.TZDateTime"


def _tz_type():
    # 引用项目统一的时区感知 DateTime 类型
    import app.database as dbmod

    return dbmod.TZDateTime()


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    if not _column_exists("assets", "last_seen_at"):
        op.add_column("assets", sa.Column("last_seen_at", _tz_type(), nullable=True))
    if not _column_exists("assets", "last_check_at"):
        op.add_column("assets", sa.Column("last_check_at", _tz_type(), nullable=True))
    if not _column_exists("assets", "unreachable_reason"):
        # 已有数据的老表：NOT NULL 需显式默认值（兼容 MySQL / SQLite）
        col = sa.Column("unreachable_reason", sa.String(length=256), nullable=False, server_default="")
        op.add_column("assets", col)


def downgrade() -> None:
    if _column_exists("assets", "unreachable_reason"):
        op.drop_column("assets", "unreachable_reason")
    if _column_exists("assets", "last_check_at"):
        op.drop_column("assets", "last_check_at")
    if _column_exists("assets", "last_seen_at"):
        op.drop_column("assets", "last_seen_at")


_ = mysql  # 保持导入一致性（迁移在两类库上运行）
