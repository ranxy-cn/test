from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ResourceLock, utcnow
from app.services.audit import add_audit, add_event

log = logging.getLogger("devops.locks")


def _aware(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        from datetime import timezone

        return dt.replace(tzinfo=timezone.utc)
    return dt


def get_lock(db: Session, asset_id: str) -> ResourceLock | None:
    lock = db.get(ResourceLock, asset_id)
    if lock is None:
        return None
    if _aware(lock.expires_at) <= utcnow():
        db.delete(lock)
        db.flush()
        return None
    return lock


def acquire_asset_lock(
    db: Session,
    *,
    asset_id: str,
    ticket_id: int,
    ttl_seconds: int | None = None,
    holder: str | None = None,
) -> tuple[bool, ResourceLock | None]:
    settings = get_settings()
    ttl = ttl_seconds or settings.lock_ttl_seconds
    holder = holder or settings.employee_id
    now = utcnow()
    existing = db.get(ResourceLock, asset_id)
    if existing is not None:
        if _aware(existing.expires_at) > now and existing.ticket_id != ticket_id:
            return False, existing
        db.delete(existing)
        db.flush()
    lock = ResourceLock(
        asset_id=asset_id,
        ticket_id=ticket_id,
        holder=holder,
        token=uuid.uuid4().hex,
        acquired_at=now,
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=ttl),
    )
    db.add(lock)
    db.flush()
    return True, lock


def heartbeat_lock(db: Session, asset_id: str, ticket_id: int, ttl_seconds: int | None = None) -> bool:
    lock = db.get(ResourceLock, asset_id)
    if lock is None or lock.ticket_id != ticket_id:
        return False
    ttl = ttl_seconds or get_settings().lock_ttl_seconds
    now = utcnow()
    lock.heartbeat_at = now
    lock.expires_at = now + timedelta(seconds=ttl)
    db.flush()
    return True


def release_asset_lock(db: Session, asset_id: str, ticket_id: int | None = None) -> None:
    lock = db.get(ResourceLock, asset_id)
    if lock is None:
        return
    if ticket_id is not None and lock.ticket_id != ticket_id:
        return
    db.delete(lock)
    db.flush()


def list_locks(db: Session) -> list[ResourceLock]:
    now = utcnow()
    rows = db.scalars(select(ResourceLock)).all()
    live = []
    for lock in rows:
        if _aware(lock.expires_at) <= now:
            db.delete(lock)
        else:
            live.append(lock)
    db.flush()
    return live


def hold_demo_lock(db: Session, asset_id: str, ttl_seconds: int = 60) -> ResourceLock:
    ok, lock = acquire_asset_lock(db, asset_id=asset_id, ticket_id=0, ttl_seconds=ttl_seconds, holder="demo-hold")
    if not ok and lock is not None:
        return lock
    add_audit(db, ticket_id=None, event_type="lock_hold", result={"asset_id": asset_id, "ttl": ttl_seconds})
    return lock


def record_lock_conflict(db: Session, ticket_id: int, asset_id: str, holder_ticket_id: int) -> None:
    add_event(
        db,
        ticket_id=ticket_id,
        kind="lock_queued",
        message=f"资产 {asset_id} 正被任务 {holder_ticket_id} 占用，本单排队等待",
        payload={"asset_id": asset_id, "holder_ticket_id": holder_ticket_id},
    )
    add_audit(
        db,
        ticket_id=ticket_id,
        event_type="lock_conflict",
        result={"asset_id": asset_id, "holder_ticket_id": holder_ticket_id, "decision": "queued"},
    )
    log.info("lock conflict asset=%s ticket=%s holder=%s", asset_id, ticket_id, holder_ticket_id)
