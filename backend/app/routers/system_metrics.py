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
from sqlalchemy import Integer, func, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Asset, SystemMetricSample, utcnow
from app.routers.agent_api import agent_status_of
from app.routers.deps import require_perm
from app.services import system_live

router = APIRouter()

_FIELDS = ("cpu", "mem", "disk", "load1", "net_rx_bps", "net_tx_bps")


def _round(v):
    return round(v, 2) if v is not None else None


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
    - 长窗口：SQL 侧按桶聚合（AVG/MAX），避免百万行加载到 Python 内存
    """
    start = utcnow() - timedelta(minutes=minutes)
    bucket = _bucket_seconds(minutes)
    base_filter = [SystemMetricSample.ts >= start]
    if asset_id:
        target = _resolve_metric_asset(db, db.get(Asset, asset_id))
        if target is None:
            return {"count": 0, "bucket_seconds": bucket, "items": []}
        base_filter.append(SystemMetricSample.asset_id == target.id)
    else:
        base_filter.append(SystemMetricSample.asset_id.is_(None))

    if not bucket:
        # 原始粒度：直接查行（≤2h ≈ 1440 行），安全上限 2000
        stmt = (
            select(SystemMetricSample)
            .where(*base_filter)
            .order_by(SystemMetricSample.ts)
            .limit(2000)
        )
        rows = db.scalars(stmt).all()
        items = [
            {**{"ts": int(r.ts.timestamp()) if r.ts else None},
             **{f: getattr(r, f) for f in _FIELDS},
             **{f"{f}_max": getattr(r, f) for f in _FIELDS}}
            for r in rows
        ]
        return {"count": len(rows), "bucket_seconds": bucket, "items": items}

    # 分桶聚合：在数据库侧 GROUP BY 时间桶，返回 avg/max（60 天 ≈ 1440 行而非百万行）
    # ts 列统一存 UTC naive（TZDateTime）。MySQL 的 UNIX_TIMESTAMP 按 session 时区解释
    # naive 值，必须把本次连接固定为 UTC 才能得到与 Python 侧 timestamp() 一致的 epoch。
    is_mysql = db.bind.dialect.name == "mysql" if db.bind else False
    if is_mysql:
        db.execute(text("SET time_zone = '+00:00'"))
        ts_expr = func.unix_timestamp(SystemMetricSample.ts)
    else:
        # SQLite：strftime('%s') 对 naive UTC 存储直接返回正确 epoch
        ts_expr = func.cast(func.strftime("%s", SystemMetricSample.ts), Integer)
    # 整数桶 ID = FLOOR(epoch / bucket)，保证同桶样本归并到同一 GROUP
    bucket_col = func.floor(ts_expr / bucket).label("bucket_id")

    cols = [bucket_col]
    for f in _FIELDS:
        col = getattr(SystemMetricSample, f)
        cols.append(func.avg(col).label(f))
        cols.append(func.max(col).label(f"{f}_max"))

    stmt = (
        select(*cols)
        .where(*base_filter)
        .group_by(bucket_col)
        .order_by(bucket_col)
    )
    rows = db.execute(stmt).all()
    items = []
    for r in rows:
        bid = int(r.bucket_id) if r.bucket_id is not None else 0
        point = {"ts": bid * bucket}
        for f in _FIELDS:
            point[f] = _round(getattr(r, f))
            point[f"{f}_max"] = _round(getattr(r, f"{f}_max"))
        items.append(point)
    return {"count": len(items), "bucket_seconds": bucket, "items": items}
