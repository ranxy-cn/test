"""系统资源实时监控端点。

- GET /api/v1/system/metrics/realtime：读一帧 /proc 快照（1 秒级轮询）
- GET /api/v1/system/metrics/history：读取后台采样线程落库的历史行
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemMetricSample, utcnow
from app.routers.deps import require_perm
from app.services import system_live

router = APIRouter()


@router.get("/api/v1/system/metrics/realtime", dependencies=[Depends(require_perm("assets:read"))])
def system_realtime():
    return system_live.read_snapshot()


@router.get("/api/v1/system/metrics/history", dependencies=[Depends(require_perm("assets:read"))])
def system_history(
    db: Session = Depends(get_db),
    minutes: float = Query(default=60, ge=1, le=1440),
):
    """应用启动以来的落库采样（无历史回填：早于启动时间的区间自然为空）。"""
    start = utcnow() - timedelta(minutes=minutes)
    rows = db.scalars(
        select(SystemMetricSample).where(SystemMetricSample.ts >= start).order_by(SystemMetricSample.ts)
    ).all()
    return {
        "count": len(rows),
        "items": [
            {
                "ts": int(r.ts.timestamp()) if r.ts else None,
                "cpu": r.cpu,
                "mem": r.mem,
                "disk": r.disk,
                "load1": r.load1,
                "net_rx_bps": r.net_rx_bps,
                "net_tx_bps": r.net_tx_bps,
                "source": r.source,
            }
            for r in rows
        ],
    }
