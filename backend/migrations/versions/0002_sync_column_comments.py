"""sync column/table comments to production schema

对应 DDL：backend/migrations/ddl/0002_sync_column_comments.sql
（列定义与 0001 完全一致，仅补 COMMENT，无结构变化。）

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20
"""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL_FILE = Path(__file__).resolve().parents[1] / "ddl" / "0002_sync_column_comments.sql"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # 本地/测试 sqlite 不跑迁移（create_all 建表），跳过
        return
    statements = [s.strip() for s in SQL_FILE.read_text(encoding="utf-8").split(";") if s.strip()]
    for stmt in statements:
        op.execute(stmt)


def downgrade() -> None:
    # 注释回填无信息损失，不做降级
    pass
