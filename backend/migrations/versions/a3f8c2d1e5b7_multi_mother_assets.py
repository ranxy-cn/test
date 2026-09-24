"""multi-mother asset fields

Revision ID: a3f8c2d1e5b7
Revises: 7c1a4f2b9d03
Create Date: 2026-09-22 14:40:00.000000

多母机管控一期：assets.kind / assets.mother_id / assets.db_mode。
kind：mother=母机（Zabbix Server 所在）/ child=子机；
mother_id：子机归属的母机资产 ID；
db_mode：母机数据库模式，bundled=独立 MySQL 容器（默认，官方镜像） / external=复用已有 MySQL。

同时把默认母机（MOTHER_ASSET_ID，即 devops-mother-186）标记为 mother，保证现网数据平滑升级。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a3f8c2d1e5b7"
down_revision = "7c1a4f2b9d03"
branch_labels = None
depends_on = None

DEFAULT_MOTHER_ID = "devops-mother-186"


def _columns(table: str) -> list[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return [c["name"] for c in insp.get_columns(table)]


def upgrade() -> None:
    cols = _columns("assets")
    if "kind" not in cols:
        op.add_column(
            "assets",
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="child",
                      comment="节点角色：mother/child"),
        )
    if "mother_id" not in cols:
        op.add_column(
            "assets",
            sa.Column("mother_id", sa.String(length=64), nullable=False, server_default="",
                      comment="归属母机资产 ID"),
        )
    if "db_mode" not in cols:
        op.add_column(
            "assets",
            sa.Column("db_mode", sa.String(length=16), nullable=False, server_default="bundled",
                      comment="母机数据库模式：bundled/external"),
        )
    # 默认母机平滑升级：把 MOTHER_ASSET_ID 对应资产置为母机
    op.execute(f"UPDATE assets SET kind='mother' WHERE id='{DEFAULT_MOTHER_ID}' AND kind='child'")


def downgrade() -> None:
    cols = _columns("assets")
    if "db_mode" in cols:
        op.drop_column("assets", "db_mode")
    if "mother_id" in cols:
        op.drop_column("assets", "mother_id")
    if "kind" in cols:
        op.drop_column("assets", "kind")
