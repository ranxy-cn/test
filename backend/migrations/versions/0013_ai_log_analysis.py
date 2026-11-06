"""AI log analysis tables

AI 日志分析：ai_analyses 分析结果表、ai_audit_logs 审计表、
anomaly_events.ai_status 联动状态列。

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    existing_cols = {c["name"] for c in sa.inspect(bind).get_columns("anomaly_events")}
    if "ai_status" not in existing_cols:
        op.add_column(
            "anomaly_events",
            sa.Column(
                "ai_status",
                sa.String(length=16),
                nullable=False,
                server_default="none",
                comment="AI 日志分析状态：none/pending/running/done/failed/skipped",
            ),
        )

    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("anomaly_id", sa.Integer(), sa.ForeignKey("anomaly_events.id"), nullable=False, comment="关联异常告警 ID"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending", comment="pending/running/done/blocked/failed/skipped"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5", comment="分析优先级 0~9（P0→0 最高）"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="", comment="AI 判定严重程度"),
        sa.Column("summary", sa.Text(), nullable=True, comment="一句话结论"),
        sa.Column("diagnosis", sa.Text(), nullable=True, comment="问题诊断"),
        sa.Column("causes", sa.JSON(), nullable=True, comment="可能原因列表"),
        sa.Column("solutions", sa.JSON(), nullable=True, comment="解决方案列表"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0", comment="置信度 0~1"),
        sa.Column("model", sa.String(128), nullable=False, server_default="", comment="模型名（mock-* 表示演示模式）"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0", comment="AI 请求耗时（毫秒）"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("0"), comment="响应是否命中操作指令过滤"),
        sa.Column("error", sa.String(1024), nullable=False, server_default="", comment="失败原因"),
        sa.Column("raw_response", sa.Text(), nullable=True, comment="模型原始响应（审计留痕）"),
        sa.Column("context_digest", sa.String(64), nullable=False, server_default="", comment="送审上下文指纹（sha256 前 16 位）"),
        sa.Column("handled_by", sa.String(64), nullable=False, server_default="", comment="人工处理人"),
        sa.Column("handled_note", sa.Text(), nullable=True, comment="人工处理备注"),
        sa.Column("handled_at", sa.DateTime(), nullable=True, comment="人工处理时间（UTC）"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="AI 日志分析结果（与异常告警双向关联）",
    )
    op.create_index("ix_ai_analyses_anomaly_id", "ai_analyses", ["anomaly_id"])
    op.create_index("ix_ai_analyses_status", "ai_analyses", ["status"])
    op.create_index("ix_ai_analyses_created_at", "ai_analyses", ["created_at"])

    op.create_table(
        "ai_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("anomaly_id", sa.Integer(), nullable=True, comment="关联异常告警 ID"),
        sa.Column("analysis_id", sa.Integer(), nullable=True, comment="关联分析结果 ID"),
        sa.Column("action", sa.String(32), nullable=False, comment="config_update/connection_test/trigger/success/failed/blocked/skipped/feedback"),
        sa.Column("operator", sa.String(64), nullable=False, server_default="system", comment="触发者"),
        sa.Column("model", sa.String(128), nullable=False, server_default="", comment="模型名"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0", comment="耗时（毫秒）"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("0"), comment="是否命中内容过滤"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="动作是否成功"),
        sa.Column("detail", sa.JSON(), nullable=True, comment="动作详情（不含密钥明文）"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="AI 操作审计日志",
    )
    op.create_index("ix_ai_audit_logs_action", "ai_audit_logs", ["action"])
    op.create_index("ix_ai_audit_logs_created_at", "ai_audit_logs", ["created_at"])
    op.create_index("ix_ai_audit_logs_anomaly_id", "ai_audit_logs", ["anomaly_id"])
    op.create_index("ix_ai_audit_logs_analysis_id", "ai_audit_logs", ["analysis_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("ai_audit_logs")
    op.drop_table("ai_analyses")
    existing_cols = {c["name"] for c in sa.inspect(bind).get_columns("anomaly_events")}
    if "ai_status" in existing_cols:
        op.drop_column("anomaly_events", "ai_status")
