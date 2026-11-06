"""Alert recovery tasks

告警恢复任务：recovery_scripts 自定义恢复脚本表、recovery_tasks 恢复任务表。
告警触发生成任务（低风险脚本自动执行/高风险等人工），告警恢复自动关闭。

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    op.create_table(
        "recovery_scripts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("name", sa.String(64), nullable=False, comment="脚本名称"),
        sa.Column("description", sa.String(256), nullable=False, server_default="", comment="用途说明"),
        sa.Column("rule_key", sa.String(64), nullable=False, server_default="", comment="匹配的告警规则 key（空=全部规则）"),
        sa.Column("risk_level", sa.String(8), nullable=False, server_default="low", comment="风险等级：low 低风险（可自动执行）/ high 高风险（需人工确认）"),
        sa.Column("command", sa.Text(), nullable=True, comment="Shell 恢复命令（目标机 root 执行）"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60", comment="执行超时（秒）"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="是否启用"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间（UTC）"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="自定义恢复脚本（告警联动自动/人工执行）",
    )
    op.create_index("ix_recovery_scripts_rule_key", "recovery_scripts", ["rule_key"])

    op.create_table(
        "recovery_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("anomaly_id", sa.Integer(), sa.ForeignKey("anomaly_events.id"), nullable=False, comment="关联异常告警 ID"),
        sa.Column("event_id", sa.String(128), nullable=False, comment="告警 event_id（幂等键）"),
        sa.Column("asset_id", sa.String(64), nullable=False, server_default="", comment="资产 ID（空=未关联资产）"),
        sa.Column("rule_key", sa.String(64), nullable=False, server_default="", comment="告警规则 key"),
        sa.Column("severity", sa.String(32), nullable=False, server_default="", comment="告警级别（P0~P3）"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50", comment="优先级分 0~99，越大越紧急（P 级映射基础分，持续未恢复叠加）"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open", comment="open 待处理 / executing 执行中 / done 完成 / cancelled 已取消"),
        sa.Column("script_id", sa.Integer(), sa.ForeignKey("recovery_scripts.id"), nullable=True, comment="命中的恢复脚本（空=纯人工任务）"),
        sa.Column("script_name", sa.String(64), nullable=False, server_default="", comment="脚本名快照"),
        sa.Column("executed_by", sa.String(64), nullable=False, server_default="", comment="执行人（system=自动执行）"),
        sa.Column("execute_ok", sa.Boolean(), nullable=True, comment="最近一次脚本执行结果"),
        sa.Column("execute_output", sa.Text(), nullable=True, comment="脚本输出留痕"),
        sa.Column("resolved_at", sa.DateTime(), nullable=True, comment="关闭时间（UTC）"),
        sa.Column("resolve_reason", sa.String(128), nullable=False, server_default="", comment="关闭原因"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间（UTC）"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        comment="告警恢复任务（告警触发生成，恢复自动关闭）",
    )
    op.create_index("ix_recovery_tasks_anomaly_id", "recovery_tasks", ["anomaly_id"])
    op.create_index("ix_recovery_tasks_event_id", "recovery_tasks", ["event_id"])
    op.create_index("ix_recovery_tasks_priority", "recovery_tasks", ["priority"])
    op.create_index("ix_recovery_tasks_status", "recovery_tasks", ["status"])
    op.create_index("ix_recovery_tasks_created_at", "recovery_tasks", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("recovery_tasks")
    op.drop_table("recovery_scripts")
