"""knowledge documents for enterprise runbooks."""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "b7d2f9a4c1e8"
down_revision = "0007"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _table_exists("knowledge_documents"):
        return
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=128), nullable=False, server_default="application/octet-stream"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="indexed"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_documents_tenant_id", "knowledge_documents", ["tenant_id"])


def downgrade() -> None:
    if _table_exists("knowledge_documents"):
        op.drop_index("ix_knowledge_documents_tenant_id", table_name="knowledge_documents")
        op.drop_table("knowledge_documents")
