"""owner_contacts：负责人通知联系人（告警推送渠道配置）

资产提交（新增子机/编辑资产）时按姓名 upsert；告警（agent 指标告警 / webhook
首次异常）与 AI 日志分析完成后，按联系人启用的渠道推送。
存量数据无该表，新建不影响既有功能；sqlite 测试库走 create_all 跳过。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.create_table(
        "owner_contacts",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False, comment="负责人姓名（与 assets.owner 对应）"),
        sa.Column("phone", sa.String(length=32), nullable=False, server_default="", comment="电话/短信接收号码"),
        sa.Column("email", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("dingtalk_webhook", sa.String(length=512), nullable=False, server_default="", comment="钉钉自定义机器人 webhook"),
        sa.Column("feishu_webhook", sa.String(length=512), nullable=False, server_default="", comment="飞书自定义机器人 webhook"),
        sa.Column("wecom_webhook", sa.String(length=512), nullable=False, server_default="", comment="企业微信群机器人 webhook"),
        sa.Column("channels", sa.JSON(), nullable=False, comment="启用的推送渠道列表"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_owner_contacts_name"),
    )
    op.create_index("ix_owner_contacts_name", "owner_contacts", ["name"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_index("ix_owner_contacts_name", table_name="owner_contacts")
    op.drop_table("owner_contacts")
