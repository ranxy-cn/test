from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import TZDateTime, Base

# 北京时间（UTC+8），用于面向用户的日期展示口径
BEIJING_TZ = timezone(timedelta(hours=8))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TicketStatus(str, enum.Enum):
    pending_analysis = "pending_analysis"
    pending_approval = "pending_approval"
    pending_execution = "pending_execution"
    executing = "executing"
    verifying = "verifying"
    recovered = "recovered"
    escalated = "escalated"
    skipped = "skipped"


class TicketSource(str, enum.Enum):
    alert = "alert"
    schedule = "schedule"
    assign = "assign"
    manual = "manual"


# ===== 登录鉴权 / RBAC =====


class User(Base):
    """平台登录用户。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 登录防爆破
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_login_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    roles: Mapped[list[Role]] = relationship(secondary="user_roles", lazy="selectin")

    @property
    def role_codes(self) -> list[str]:
        return sorted(r.code for r in self.roles)


class Role(Base):
    """角色（admin / operator / viewer）。"""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", lazy="selectin")


class Permission(Base):
    """权限点（模块:动作），如 tickets:operate。"""

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class Menu(Base):
    """菜单/路由/按钮统一资源树。

    type=dir 目录；type=menu 页面菜单（path 为前端路由，perm_code 为进入页面所需权限点）；
    type=button 页面按钮（perm_code 为按钮所需权限点，前端 v-perm 依据）。
    """

    __tablename__ = "menus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("menus.id", ondelete="CASCADE"), nullable=True, comment="父菜单 ID（空=顶级）"
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="资源唯一编码，如 menu:tickets / btn:ticket-approve")
    name: Mapped[str] = mapped_column(String(64), comment="显示名称")
    type: Mapped[str] = mapped_column(String(16), comment="类型：dir 目录 / menu 菜单 / button 按钮")
    path: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="前端路由路径（menu 型必填）")
    perm_code: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所需权限点编码（关联 permissions.code）")
    icon: Mapped[str] = mapped_column(String(64), default="", comment="图标名称（前端图标映射）")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序值（小的在前）")
    visible: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否在侧边栏显示：1 显示 / 0 隐藏")
    status: Mapped[str] = mapped_column(String(16), default="enabled", comment="状态：enabled 启用 / disabled 停用")
    remark: Mapped[str] = mapped_column(String(256), default="", comment="备注")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, comment="创建时间（UTC）")
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, comment="更新时间（UTC）"
    )


class RoleMenu(Base):
    """角色-菜单授权关联（多对多）。"""

    __tablename__ = "role_menus"

    role_id: Mapped[int] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, comment="角色 ID（fk → roles.id，级联删除）"
    )
    menu_id: Mapped[int] = mapped_column(
        ForeignKey("menus.id", ondelete="CASCADE"), primary_key=True, comment="菜单 ID（fk → menus.id，级联删除）"
    )


class DictEntry(Base):
    """业务字典（枚举中文名映射）：告警标题 / 资产 / 预案等 code → 中文展示。"""

    __tablename__ = "dict_entries"
    __table_args__ = (UniqueConstraint("dict_type", "code", name="uq_dict_type_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="自增主键")
    dict_type: Mapped[str] = mapped_column(String(32), index=True, comment="字典类型：trigger 告警标题 / asset 资产 / action 预案")
    code: Mapped[str] = mapped_column(String(128), comment="业务编码（原值，如 CPU usage > 85% for 5 minutes）")
    label: Mapped[str] = mapped_column(String(128), comment="中文展示名")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序值（小的在前）")
    status: Mapped[str] = mapped_column(String(16), default="enabled", comment="状态：enabled 启用 / disabled 停用")
    remark: Mapped[str] = mapped_column(String(256), default="", comment="备注")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, comment="创建时间（UTC）")
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, comment="更新时间（UTC）"
    )


class UserToken(Base):
    """访问令牌白名单：JWT jti 落库，支持登出/改密后强制失效。"""

    __tablename__ = "user_tokens"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    issued_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class LoginLog(Base):
    """登录审计（成功/失败/锁定原因）。"""

    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    fail_reason: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)


# ===== 业务表 =====


class DigitalEmployee(Base):
    __tablename__ = "digital_employees"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    team: Mapped[str] = mapped_column(String(128))
    systems: Mapped[list] = mapped_column(JSON, default=list)
    manager: Mapped[str] = mapped_column(String(64))
    oncall: Mapped[str] = mapped_column(String(64))
    skill_version: Mapped[str] = mapped_column(String(64))
    auth_expires_at: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="active")
    duties: Mapped[list] = mapped_column(JSON, default=list)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), unique=True)
    app: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(64))
    env: Mapped[str] = mapped_column(String(32), default="prod")
    owner: Mapped[str] = mapped_column(String(64))
    group: Mapped[str] = mapped_column(String(64), default="", comment="业务分组，如订单系统/财务系统")
    kind: Mapped[str] = mapped_column(String(16), default="child", comment="节点角色：mother=母机（Zabbix Server 所在）/ child=子机")
    mother_id: Mapped[str] = mapped_column(String(64), default="", comment="归属母机资产 ID（子机字段；母机为空）")
    db_mode: Mapped[str] = mapped_column(String(16), default="bundled", comment="母机数据库模式：bundled=独立 MySQL 容器镜像 / external=复用已有 MySQL（母机字段）")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    db_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    unreachable_reason: Mapped[str] = mapped_column(String(256), default="")
    last_restart_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    external_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    zabbix_host: Mapped[str] = mapped_column(String(128), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    reason: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[datetime] = mapped_column(TZDateTime)
    ends_at: Mapped[datetime] = mapped_column(TZDateTime)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), default=TicketSource.alert.value)
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.pending_analysis.value, index=True)
    employee_id: Mapped[str] = mapped_column(String(32), default="DE-OPS-001")
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    tenant_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    trigger_name: Mapped[str] = mapped_column(String(256), default="")
    severity: Mapped[str] = mapped_column(String(32), default="high")
    action_type: Mapped[str] = mapped_column(String(64), default="PROBLEM")
    job_version: Mapped[str] = mapped_column(String(64), default="v1")
    event_id: Mapped[str] = mapped_column(String(128))
    owner: Mapped[str] = mapped_column(String(64), default="")
    risk_level: Mapped[str] = mapped_column(String(16), default="unknown")
    policy_light: Mapped[str | None] = mapped_column(String(16), nullable=True)
    candidate_action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playbook_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    params_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    demo_scenario: Mapped[str] = mapped_column(String(32), default="green")
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    human_wait_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    approval_requested_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    escalate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    asset: Mapped[Asset] = relationship()
    events: Mapped[list[TicketEvent]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    approvals: Mapped[list[Approval]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="events")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(64))
    playbook_id: Mapped[str] = mapped_column(String(64))
    playbook_version: Mapped[str] = mapped_column(String(32))
    params_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    approver: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="approvals")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playbook_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approver: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_alert_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    asset_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class AnomalyEvent(Base):
    """异常告警条目：只有「异常 / 恢复」两种状态，按 Zabbix event_id 幂等更新。"""

    __tablename__ = "anomaly_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    hostid: Mapped[str] = mapped_column(String(32), default="")
    host: Mapped[str] = mapped_column(String(128), default="")
    hostname: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    trigger_name: Mapped[str] = mapped_column(String(256), default="")
    severity: Mapped[str] = mapped_column(String(32), default="high")
    message: Mapped[str] = mapped_column(String(256), default="")
    # abnormal 异常 / recovered 恢复
    status: Mapped[str] = mapped_column(String(16), default="abnormal", index=True)
    asset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="异常时刻进程快照（TOP 进程/负载/内存/监听/会话）")
    # none 未采集 / running 采集中 / done 完成 / failed 失败
    diag_status: Mapped[str] = mapped_column(String(16), default="none")
    diag_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    diag_error: Mapped[str] = mapped_column(String(512), default="")
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    recovered_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class AnomalyLog(Base):
    """异常告警原始通知日志：每条 Zabbix webhook 推送（异常/恢复）都留痕，保留完整原始载荷。"""

    __tablename__ = "anomaly_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anomaly_id: Mapped[int] = mapped_column(ForeignKey("anomaly_events.id"), index=True)
    event_id: Mapped[str] = mapped_column(String(128), default="")
    # problem 异常通知 / recovered 恢复通知
    action: Mapped[str] = mapped_column(String(16), default="problem")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class ResourceLock(Base):
    __tablename__ = "resource_locks"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(Integer, index=True)
    holder: Mapped[str] = mapped_column(String(64), default="DE-OPS-001")
    token: Mapped[str] = mapped_column(String(64))
    acquired_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)


class ActionFailure(Base):
    __tablename__ = "action_failures"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    failed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="inbox")
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    asset_id: Mapped[str] = mapped_column(String(64), index=True)
    schedule: Mapped[str] = mapped_column(String(64), default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_backup_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_restore_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("backup_jobs.id"), index=True)
    backup_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    restore_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    note: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
