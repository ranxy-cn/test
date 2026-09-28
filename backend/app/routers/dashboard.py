from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnomalyEvent, Asset, Ticket
from app.routers.deps import require_perm

router = APIRouter()


@router.get("/api/v1/dashboard/overview", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_overview(db: Session = Depends(get_db)):
    assets = db.scalars(select(Asset).order_by(Asset.hostname)).all()
    active_count = db.scalar(
        select(func.count()).select_from(AnomalyEvent).where(AnomalyEvent.status == "abnormal")
    ) or 0
    active_anomalies = db.scalars(
        select(AnomalyEvent).where(AnomalyEvent.status == "abnormal").order_by(AnomalyEvent.last_seen_at.desc()).limit(10)
    ).all()
    open_tickets = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status.not_in(["recovered", "skipped"]))
    ) or 0
    recovered_tickets = db.scalar(
        select(func.count()).select_from(Ticket).where(Ticket.status == "recovered")
    ) or 0
    since = datetime.now(timezone.utc) - timedelta(days=7)
    trend_rows = db.execute(
        select(func.date(AnomalyEvent.last_seen_at), func.count())
        .where(AnomalyEvent.last_seen_at >= since)
        .group_by(func.date(AnomalyEvent.last_seen_at))
        .order_by(func.date(AnomalyEvent.last_seen_at))
    ).all()
    return {
        "generated_at": datetime.now(timezone.utc),
        "kpis": {
            "asset_total": len(assets),
            "asset_reachable": sum(1 for asset in assets if asset.reachable),
            "active_anomalies": active_count,
            "open_tickets": open_tickets,
            "recovered_tickets": recovered_tickets,
            "availability": round(sum(1 for asset in assets if asset.reachable) / len(assets) * 100, 1) if assets else 100,
        },
        "trend": [{"date": str(day), "count": count} for day, count in trend_rows],
        "assets": [
            {"hostname": asset.hostname, "app": asset.app, "group": asset.group, "reachable": asset.reachable, "db_ok": asset.db_ok}
            for asset in assets[:30]
        ],
        "anomalies": [
            {"hostname": row.hostname or row.host, "trigger": row.trigger_name, "severity": row.severity, "message": row.message, "last_seen_at": row.last_seen_at}
            for row in active_anomalies
        ],
    }
