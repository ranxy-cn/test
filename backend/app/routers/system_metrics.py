"""系统资源监控端点（母机本机 /proc + 子机 Agent 上报，按资产隔离）。

- GET /api/v1/system/metrics/realtime?asset_id=
    不带 asset_id：平台本机 /proc 快照（1 秒级轮询）
    带 asset_id：子机=该子机 Agent 最新一帧（内存缓存）+ 在线状态；
    母机=其本机子机（extra.self_child_id）的 Agent 数据，无本机子机返回离线空值
- GET /api/v1/system/metrics/history?minutes=&asset_id=
    落库查询，最长 2 个月；长窗口自动分桶降采样（桶内 avg/max）。
    子机曲线=该资产样本；母机曲线=其本机子机样本（无本机子机则为空）；不带 asset_id=平台本机采样。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Asset, SystemMetricSample, utcnow
from app.routers.agent_api import agent_status_of
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


def _resolve_metric_asset(db: Session, asset: Asset | None) -> Asset | None:
    """母机的监控数据由其本机子机 agent 提供（新增母机时自动纳管产生）。"""
    if asset is not None and asset.kind == "mother":
        cid = str((asset.extra or {}).get("self_child_id") or "")
        if cid:
            child = db.get(Asset, cid)
            if child is not None:
                return child
    return asset


def _asset_latest(db: Session, asset_id: str) -> dict:
    asset = _resolve_metric_asset(db, db.get(Asset, asset_id))
    if not asset:
        return {"supported": False, "note": "资产不存在"}
    status = agent_status_of(asset, db)
    latest = status["latest"]
    return {
        "supported": bool(status["enabled"]),
        "note": "" if status["enabled"] else "未部署 Agent",
        "online": status["online"],
        "last_seen": status["last_seen"],
        "version": status["version"],
        "ts": latest.get("ts"),
        "cpu": latest.get("cpu"),
        "mem": latest.get("mem"),
        "disk": latest.get("disk"),
        "load1": latest.get("load1"),
        "net_rx_bps": latest.get("net_rx_bps"),
        "net_tx_bps": latest.get("net_tx_bps"),
        "net_iface": latest.get("iface", ""),
    }


@router.get("/api/v1/system/metrics/realtime", dependencies=[Depends(require_perm("assets:read"))])
def system_realtime(
    db: Session = Depends(get_db),
    asset_id: str = Query(default=""),
):
    if asset_id:
        return _asset_latest(db, asset_id)
    return system_live.read_snapshot()


@router.get("/api/v1/system/metrics/history", dependencies=[Depends(require_perm("assets:read"))])
def system_history(
    db: Session = Depends(get_db),
    minutes: float = Query(default=60, ge=1, le=86400),
    asset_id: str = Query(default=""),
):
    """落库采样查询（子机=Agent 上报；母机=其本机子机曲线；不带 asset_id=平台本机采样）。

    - 原始粒度（≤2h）：bucket_seconds=0，每行原样返回（*_max 与均值相同）
    - 长窗口：按桶聚合，每桶返回均值与峰值（cpu_max 等），点数 ≤ 窗口秒数/桶宽
    """
    start = utcnow() - timedelta(minutes=minutes)
    stmt = select(SystemMetricSample).where(SystemMetricSample.ts >= start)
    if asset_id:
        # 母机曲线 = 其本机子机 agent 上报；无本机子机的母机没有真实数据，返回空（不伪造）
        target = _resolve_metric_asset(db, db.get(Asset, asset_id))
        if target is None:
            return {"count": 0, "bucket_seconds": _bucket_seconds(minutes), "items": []}
        stmt = stmt.where(SystemMetricSample.asset_id == target.id)
    else:
        stmt = stmt.where(SystemMetricSample.asset_id.is_(None))  # 平台本机采样
    rows = db.scalars(stmt.order_by(SystemMetricSample.ts)).all()
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
