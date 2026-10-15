"""assets.ssh_password_enc：子机 SSH 密码加密落库

SSH 终端 / 卸载 Agent 凭据来源：资产保存的 SSH 密码（Fernet 加密，
密钥口径同 AI 配置加密）→ 请求临时密码 → 免密巡检私钥兜底。
存量资产列为空串，行为不变（回退原通道），完全兼容历史数据。

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.add_column(
        "assets",
        sa.Column(
            "ssh_password_enc",
            sa.String(length=512),
            nullable=False,
            server_default="",
            comment="SSH 密码密文（Fernet；配置入口：新增/编辑子机页面）",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_column("assets", "ssh_password_enc")
