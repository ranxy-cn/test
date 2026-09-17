from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ActionFailure, utcnow


def record_action_failure(db: Session, asset_id: str, action_id: str, reason: str) -> None:
    if not action_id:
        return
    row = db.get(ActionFailure, (asset_id, action_id))
    if row is None:
        db.add(ActionFailure(asset_id=asset_id, action_id=action_id, reason=reason, failed_at=utcnow()))
    else:
        row.reason = reason
        row.failed_at = utcnow()
    db.flush()


def last_action_failure(db: Session, asset_id: str, action_id: str | None) -> datetime | None:
    if not action_id:
        return None
    row = db.get(ActionFailure, (asset_id, action_id))
    return row.failed_at if row else None


def action_fail_cooldown_violated(failed_at: datetime | None, now: datetime | None = None) -> bool:
    if failed_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    last = failed_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() < get_settings().action_fail_cooldown_seconds
