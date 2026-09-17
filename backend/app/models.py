from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base


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
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    db_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    last_restart_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    reason: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    approval_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResourceLock(Base):
    __tablename__ = "resource_locks"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(Integer, index=True)
    holder: Mapped[str] = mapped_column(String(64), default="DE-OPS-001")
    token: Mapped[str] = mapped_column(String(64))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionFailure(Base):
    __tablename__ = "action_failures"

    asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    asset_id: Mapped[str] = mapped_column(String(64), index=True)
    schedule: Mapped[str] = mapped_column(String(64), default="daily")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
