from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import AnomalyEvent, Asset, Ticket
from app.routers.deps import require_perm
from app.services.netdata import collect_snapshot

router = APIRouter()


def _netdata_summary(snapshot: dict) -> dict:
    return {
        "asset_id": snapshot["asset_id"],
        "hostname": snapshot["hostname"],
        "configured": snapshot["configured"],
        "online": snapshot["online"],
        "metrics": snapshot.get("metrics") or {},
        "info": snapshot.get("info") or {},
        "error": snapshot.get("error", ""),
        "checked_at": snapshot.get("checked_at"),
    }


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


@router.get("/api/v1/dashboard/netdata", dependencies=[Depends(require_perm("dashboard:read"))])
def dashboard_netdata(
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1, le=24),
    minutes: int = Query(default=5, ge=1, le=60),
):
    """独立大屏使用的 Netdata 聚合接口。

    每台资产独立降级，某一台 Netdata 离线不会阻断其他资产和既有大屏数据。
    """
    asset_limit = limit or get_settings().netdata_max_assets
    assets = db.scalars(select(Asset).order_by(Asset.hostname).limit(asset_limit)).all()
    if not assets:
        return {"generated_at": datetime.now(timezone.utc), "online_count": 0, "configured_count": 0, "items": [], "featured": None}

    with ThreadPoolExecutor(max_workers=min(6, len(assets))) as pool:
        snapshots = list(pool.map(lambda asset: collect_snapshot(asset, minutes), assets))
    items = [_netdata_summary(snapshot) for snapshot in snapshots]
    featured = next((snapshot for snapshot in snapshots if snapshot.get("online")), None)
    if featured is None:
        featured = next((snapshot for snapshot in snapshots if snapshot.get("configured")), None)
    return {
        "generated_at": datetime.now(timezone.utc),
        "online_count": sum(1 for item in items if item["online"]),
        "configured_count": sum(1 for item in items if item["configured"]),
        "items": items,
        "featured": featured,
    }


@router.get("/api/v1/assets/{asset_id}/netdata", dependencies=[Depends(require_perm("assets:read"))])
def asset_netdata(
    asset_id: str,
    db: Session = Depends(get_db),
    minutes: int = Query(default=5, ge=1, le=60),
):
    """读取单台资产的 Netdata 实时指标。"""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "资产不存在")
    return collect_snapshot(asset, minutes)
