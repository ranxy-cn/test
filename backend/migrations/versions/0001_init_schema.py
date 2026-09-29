"""init schema: business tables + rbac auth

数据库初始结构（V1）：
- 业务表：digital_employees / assets / maintenance_windows / tickets /
  ticket_events / approvals / audit_logs / alert_events / resource_locks /
  action_failures / notifications / backup_jobs / backup_runs
- 鉴权表：users / roles / permissions / user_roles / role_permissions /
  user_tokens / login_logs

Revision ID: 0001
Revises:
Create Date: 2026-09-20 10:49:40.531679
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from app.database import TZDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('action_failures',
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('action_id', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('failed_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('asset_id', 'action_id')
    )
    op.create_table('alert_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('idempotency_key', sa.String(length=256), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('skipped', sa.Boolean(), nullable=False),
    sa.Column('skip_reason', sa.String(length=64), nullable=True),
    sa.Column('ticket_id', sa.Integer(), nullable=True),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key', name='uq_alert_idempotency')
    )
    op.create_index(op.f('ix_alert_events_idempotency_key'), 'alert_events', ['idempotency_key'], unique=False)
    op.create_table('assets',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('hostname', sa.String(length=128), nullable=False),
    sa.Column('app', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=64), nullable=False),
    sa.Column('env', sa.String(length=32), nullable=False),
    sa.Column('owner', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('reachable', sa.Boolean(), nullable=False),
    sa.Column('db_ok', sa.Boolean(), nullable=False),
    sa.Column('last_restart_at', TZDateTime(), nullable=True),
    sa.Column('external_id', sa.String(length=64), nullable=False),
    sa.Column('zabbix_host', sa.String(length=128), nullable=False),
    sa.Column('extra', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hostname')
    )
    op.create_index(op.f('ix_assets_external_id'), 'assets', ['external_id'], unique=False)
    op.create_index(op.f('ix_assets_tenant_id'), 'assets', ['tenant_id'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('evidence_refs', sa.JSON(), nullable=False),
    sa.Column('model_version', sa.String(length=64), nullable=True),
    sa.Column('policy_version', sa.String(length=64), nullable=True),
    sa.Column('playbook_version', sa.String(length=64), nullable=True),
    sa.Column('approver', sa.String(length=64), nullable=True),
    sa.Column('params_digest', sa.String(length=64), nullable=True),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_event_type'), 'audit_logs', ['event_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_ticket_id'), 'audit_logs', ['ticket_id'], unique=False)
    op.create_table('backup_jobs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('schedule', sa.String(length=64), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('last_run_at', TZDateTime(), nullable=True),
    sa.Column('last_backup_ok', sa.Boolean(), nullable=True),
    sa.Column('last_restore_verified', sa.Boolean(), nullable=True),
    sa.Column('note', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_backup_jobs_asset_id'), 'backup_jobs', ['asset_id'], unique=False)
    op.create_table('digital_employees',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('team', sa.String(length=128), nullable=False),
    sa.Column('systems', sa.JSON(), nullable=False),
    sa.Column('manager', sa.String(length=64), nullable=False),
    sa.Column('oncall', sa.String(length=64), nullable=False),
    sa.Column('skill_version', sa.String(length=64), nullable=False),
    sa.Column('auth_expires_at', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('duties', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('login_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('fail_reason', sa.String(length=64), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_login_logs_created_at'), 'login_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_login_logs_ip'), 'login_logs', ['ip'], unique=False)
    op.create_index(op.f('ix_login_logs_user_id'), 'login_logs', ['user_id'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=True),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('channel', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_kind'), 'notifications', ['kind'], unique=False)
    op.create_index(op.f('ix_notifications_ticket_id'), 'notifications', ['ticket_id'], unique=False)
    op.create_table('permissions',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.String(length=256), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_permissions_code'), 'permissions', ['code'], unique=True)
    op.create_table('resource_locks',
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('holder', sa.String(length=64), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('acquired_at', TZDateTime(), nullable=False),
    sa.Column('heartbeat_at', TZDateTime(), nullable=False),
    sa.Column('expires_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('asset_id')
    )
    op.create_index(op.f('ix_resource_locks_ticket_id'), 'resource_locks', ['ticket_id'], unique=False)
    op.create_table('roles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.String(length=256), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_roles_code'), 'roles', ['code'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('username', sa.String(length=64), nullable=False),
    sa.Column('password_hash', sa.String(length=128), nullable=False),
    sa.Column('display_name', sa.String(length=64), nullable=False),
    sa.Column('email', sa.String(length=128), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('locked_until', TZDateTime(), nullable=True),
    sa.Column('last_login_at', TZDateTime(), nullable=True),
    sa.Column('last_login_ip', sa.String(length=64), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.Column('updated_at', TZDateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('backup_runs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('job_id', sa.String(length=64), nullable=False),
    sa.Column('backup_ok', sa.Boolean(), nullable=True),
    sa.Column('restore_verified', sa.Boolean(), nullable=True),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('started_at', TZDateTime(), nullable=False),
    sa.Column('finished_at', TZDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['backup_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_backup_runs_job_id'), 'backup_runs', ['job_id'], unique=False)
    op.create_table('maintenance_windows',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.String(length=256), nullable=False),
    sa.Column('starts_at', TZDateTime(), nullable=False),
    sa.Column('ends_at', TZDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_maintenance_windows_asset_id'), 'maintenance_windows', ['asset_id'], unique=False)
    op.create_table('role_permissions',
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.Column('permission_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['permissions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id')
    )
    op.create_table('tickets',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('number', sa.String(length=32), nullable=False),
    sa.Column('idempotency_key', sa.String(length=256), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('employee_id', sa.String(length=32), nullable=False),
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('trigger_name', sa.String(length=256), nullable=False),
    sa.Column('severity', sa.String(length=32), nullable=False),
    sa.Column('action_type', sa.String(length=64), nullable=False),
    sa.Column('job_version', sa.String(length=64), nullable=False),
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('owner', sa.String(length=64), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('policy_light', sa.String(length=16), nullable=True),
    sa.Column('candidate_action_id', sa.String(length=64), nullable=True),
    sa.Column('playbook_version', sa.String(length=32), nullable=True),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('params_digest', sa.String(length=64), nullable=True),
    sa.Column('diagnosis', sa.JSON(), nullable=True),
    sa.Column('evidence', sa.JSON(), nullable=True),
    sa.Column('policy_result', sa.JSON(), nullable=True),
    sa.Column('demo_scenario', sa.String(length=32), nullable=False),
    sa.Column('execution_count', sa.Integer(), nullable=False),
    sa.Column('human_wait_seconds', sa.Float(), nullable=False),
    sa.Column('approval_requested_at', TZDateTime(), nullable=True),
    sa.Column('escalate_reason', sa.Text(), nullable=True),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.Column('updated_at', TZDateTime(), nullable=False),
    sa.Column('closed_at', TZDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tickets_idempotency_key'), 'tickets', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_tickets_number'), 'tickets', ['number'], unique=True)
    op.create_index(op.f('ix_tickets_status'), 'tickets', ['status'], unique=False)
    op.create_table('user_roles',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['role_id'], ['roles.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id')
    )
    op.create_table('user_tokens',
    sa.Column('jti', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('issued_at', TZDateTime(), nullable=False),
    sa.Column('expires_at', TZDateTime(), nullable=False),
    sa.Column('revoked_at', TZDateTime(), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('jti')
    )
    op.create_index(op.f('ix_user_tokens_expires_at'), 'user_tokens', ['expires_at'], unique=False)
    op.create_index(op.f('ix_user_tokens_user_id'), 'user_tokens', ['user_id'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.String(length=64), nullable=False),
    sa.Column('playbook_id', sa.String(length=64), nullable=False),
    sa.Column('playbook_version', sa.String(length=32), nullable=False),
    sa.Column('params_digest', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('approver', sa.String(length=64), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('expires_at', TZDateTime(), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.Column('decided_at', TZDateTime(), nullable=True),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approvals_ticket_id'), 'approvals', ['ticket_id'], unique=False)
    op.create_table('ticket_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ticket_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', TZDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ticket_events_ticket_id'), 'ticket_events', ['ticket_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ticket_events_ticket_id'), table_name='ticket_events')
    op.drop_table('ticket_events')
    op.drop_index(op.f('ix_approvals_ticket_id'), table_name='approvals')
    op.drop_table('approvals')
    op.drop_index(op.f('ix_user_tokens_user_id'), table_name='user_tokens')
    op.drop_index(op.f('ix_user_tokens_expires_at'), table_name='user_tokens')
    op.drop_table('user_tokens')
    op.drop_table('user_roles')
    op.drop_index(op.f('ix_tickets_status'), table_name='tickets')
    op.drop_index(op.f('ix_tickets_number'), table_name='tickets')
    op.drop_index(op.f('ix_tickets_idempotency_key'), table_name='tickets')
    op.drop_table('tickets')
    op.drop_table('role_permissions')
    op.drop_index(op.f('ix_maintenance_windows_asset_id'), table_name='maintenance_windows')
    op.drop_table('maintenance_windows')
    op.drop_index(op.f('ix_backup_runs_job_id'), table_name='backup_runs')
    op.drop_table('backup_runs')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_roles_code'), table_name='roles')
    op.drop_table('roles')
    op.drop_index(op.f('ix_resource_locks_ticket_id'), table_name='resource_locks')
    op.drop_table('resource_locks')
    op.drop_index(op.f('ix_permissions_code'), table_name='permissions')
    op.drop_table('permissions')
    op.drop_index(op.f('ix_notifications_ticket_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_kind'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_login_logs_user_id'), table_name='login_logs')
    op.drop_index(op.f('ix_login_logs_ip'), table_name='login_logs')
    op.drop_index(op.f('ix_login_logs_created_at'), table_name='login_logs')
    op.drop_table('login_logs')
    op.drop_table('digital_employees')
    op.drop_index(op.f('ix_backup_jobs_asset_id'), table_name='backup_jobs')
    op.drop_table('backup_jobs')
    op.drop_index(op.f('ix_audit_logs_ticket_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_event_type'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_assets_tenant_id'), table_name='assets')
    op.drop_index(op.f('ix_assets_external_id'), table_name='assets')
    op.drop_table('assets')
    op.drop_index(op.f('ix_alert_events_idempotency_key'), table_name='alert_events')
    op.drop_table('alert_events')
    op.drop_table('action_failures')
