"""系统资源实时监控端点。

- GET /api/v1/system/metrics/realtime：读一帧 /proc 快照（1 秒级轮询）
- GET /api/v1/system/metrics/history：查询落库历史，最长 2 个月；
  长窗口自动分桶降采样（桶内 avg/max），保证返回点数适合直接画图
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

_FIELDS = ("cpu", "mem", "disk", "load1", "net_rx_bps", "net_tx_bps")


def _avg(vals: list) -> float | None:
    """均值：忽略 None；全空返回 None。"""
    xs = [v for v in vals if v is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def _peak(vals: list) -> float | None:
    xs = [v for v in vals if v is not None]
    return round(max(xs), 2) if xs else None


def _bucket_seconds(minutes: float) -> int:
    """窗口 ≤2h 用原始 5s 粒度；≤2 天 5 分钟桶；更长 1 小时桶（60 天=1440 点）。"""
    if minutes * 60 <= 2 * 3600:
        return 0
    if minutes * 60 <= 2 * 86400:
        return 300
    return 3600


@router.get("/api/v1/system/metrics/realtime", dependencies=[Depends(require_perm("assets:read"))])
def system_realtime():
    return system_live.read_snapshot()


@router.get("/api/v1/system/metrics/history", dependencies=[Depends(require_perm("assets:read"))])
def system_history(
    db: Session = Depends(get_db),
    minutes: float = Query(default=60, ge=1, le=86400),
):
    """落库采样查询（应用启动后开始记录，早于启动的区间自然为空）。

    - 原始粒度（≤2h）：bucket_seconds=0，每行原样返回（*_max 与均值相同）
    - 长窗口：按桶聚合，每桶返回均值与峰值（cpu_max 等），点数 ≤ 窗口秒数/桶宽
    """
    start = utcnow() - timedelta(minutes=minutes)
    rows = db.scalars(
        select(SystemMetricSample).where(SystemMetricSample.ts >= start).order_by(SystemMetricSample.ts)
    ).all()
    bucket = _bucket_seconds(minutes)
    if not bucket:
        items = [
            {**{"ts": int(r.ts.timestamp()) if r.ts else None},
             **{f: getattr(r, f) for f in _FIELDS},
             **{f"{f}_max": getattr(r, f) for f in _FIELDS}}
            for r in rows
        ]
    else:
        groups: dict[int, list[SystemMetricSample]] = {}
        for r in rows:
            groups.setdefault(int(r.ts.timestamp()) // bucket, []).append(r)
        items = []
        for k in sorted(groups):
            g = groups[k]
            point = {"ts": k * bucket}
            for f in _FIELDS:
                vals = [getattr(r, f) for r in g]
                point[f] = _avg(vals)
                point[f"{f}_max"] = _peak(vals)
            items.append(point)
    return {"count": len(rows), "bucket_seconds": bucket, "items": items}
