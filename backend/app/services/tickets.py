from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BEIJING_TZ, Ticket, utcnow


def next_ticket_number(db: Session, now: datetime | None = None) -> str:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    prefix = f"TKT-{now.astimezone(BEIJING_TZ).strftime('%Y%m%d')}-"
    count = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.number.like(f"{prefix}%"))) or 0
    return f"{prefix}{count + 1:04d}"


def make_idempotency_key(event_id: str, asset_id: str, job_version: str, action_type: str) -> str:
    return f"{event_id}|{asset_id}|{job_version}|{action_type}"


def employee_id() -> str:
    return get_settings().employee_id
