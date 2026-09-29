from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog, TicketEvent


def add_event(
    db: Session,
    *,
    ticket_id: int,
    kind: str,
    message: str,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
) -> TicketEvent:
    ev = TicketEvent(
        ticket_id=ticket_id,
        kind=kind,
        actor=actor or get_settings().employee_id,
        message=message,
        payload=payload or {},
    )
    db.add(ev)
    return ev


def add_audit(
    db: Session,
    *,
    ticket_id: int | None,
    event_type: str,
    result: dict[str, Any],
    actor: str | None = None,
    evidence_refs: list | None = None,
    model_version: str | None = None,
    policy_version: str | None = None,
    playbook_version: str | None = None,
    approver: str | None = None,
    params_digest: str | None = None,
) -> AuditLog:
    row = AuditLog(
        ticket_id=ticket_id,
        actor=actor or get_settings().employee_id,
        event_type=event_type,
        evidence_refs=evidence_refs or [],
        model_version=model_version,
        policy_version=policy_version,
        playbook_version=playbook_version,
        approver=approver,
        params_digest=params_digest,
        result=result,
    )
    db.add(row)
    return row
