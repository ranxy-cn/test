"""监控指标采样与趋势分析服务。

- 采集：run_collect_cycle 把每台资产的 CPU/内存/磁盘/负载定时落库（5 分钟粒度，幂等）
- 查询：query_series 时间窗口序列（自动降采样）；query_compare 多日对比（平移对齐到当前时间轴）
- 基线：build_baseline 按「一天内的时段」建立正常范围（P05~P95），历史越充足越准
- 异常：detect_anomalies 当前序列显著偏离历史基线且连续出现的点
- 预测：forecast_series 线性回归（OLS）外推未来趋势

这里同时承载 zabbix_client_for / mock_asset_metrics（原 api.py 内的辅助函数），
供 api 层与 celery 采集任务共用，避免 api -> services -> api 循环导入。
"""
from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import TZDateTime  # noqa: F401  （保持与 models 一致的类型语义）
from app.models import Asset, MetricSample, utcnow

METRIC_KEYS = ("cpu", "mem", "disk", "load")
# 序列键 load 对应落库字段 load1（1 分钟负载）
_DB_FIELD = {"load": "load1"}
# 降采样目标点数上限（24h 数据按 2 分钟桶 ≈ 720 点，前端渲染流畅）
_MAX_POINTS = 720


def _db_col(metric: str) -> str:
    return _DB_FIELD.get(metric, metric)


# ===== Zabbix 客户端解析（自 api.py 移入） =====


def _zabbix_cfg(mother: Asset | None) -> dict:
    """母机上登记的 Zabbix 实例连接信息（extra["zabbix"]）。空 dict 表示回落全局配置。"""
    z = ((mother.extra or {}).get("zabbix") or {}) if mother else {}
    return z if z.get("url") else {}


def zabbix_client_for(db: Session, asset: Asset):
    """按资产所属母机实例化 Zabbix 客户端：优先母机登记的实例，否则回落全局配置。"""
    from app.integrations.zabbix.http import build_http_zabbix

    settings = get_settings()
    mother_id = asset.mother_id or (asset.id if asset.kind == "mother" else "") or settings.mother_asset_id
    mother = db.get(Asset, mother_id)
    cfg = _zabbix_cfg(mother)
    if cfg:
        from app.integrations.zabbix.http import HttpZabbixClient

        return HttpZabbixClient(
            cfg["url"],
            cfg.get("token", ""),
            username=cfg.get("user", ""),
            password=cfg.get("password", ""),
            timeout=settings.zabbix_timeout_seconds,
            retries=settings.zabbix_retries,
            verify_ssl=cfg.get("verify_ssl", True),
        )
    return build_http_zabbix()


def resolve_real_client(db: Session, asset: Asset):
    """real 模式且存在 Zabbix 配置时返回客户端；否则 None（采集走 mock 演示序列）。"""
    settings = get_settings()
    if settings.integration_mode != "real":
        return None
    if settings.zabbix_url or _zabbix_cfg(db.get(Asset, _mother_id_of(asset))):
        return zabbix_client_for(db, asset)
    return None


def _mother_id_of(asset: Asset) -> str:
    settings = get_settings()
    return asset.mother_id or (asset.id if asset.kind == "mother" else "") or settings.mother_asset_id


def host_hint_of(asset: Asset) -> dict:
    hint = {"external_id": asset.external_id, "zabbix_host": asset.zabbix_host, "hostname": asset.hostname}
    if asset.kind == "mother":
        # 母机自身监控由 Zabbix 栈自带的 agent 容器上报，主机名固定为 "Zabbix server"
        hint["zabbix_host"] = "Zabbix server"
    return hint


# ===== mock 演示序列（自 api.py 移入） =====


def mock_asset_metrics(asset_id: str, minutes: int) -> dict:
    """Zabbix 未接入（mock 模式）时的演示序列：最近 N 分钟、每分钟一个点。"""
    now = int(utcnow().timestamp())
    n = max(10, min(int(minutes), 240))

    def walk(base: float, spread: float, low: float, high: float) -> list:
        vals, cur = [], base
        for i in range(n):
            cur = min(high, max(low, cur + random.uniform(-spread, spread) + (base - cur) * 0.1))
            vals.append({"t": str(now - (n - 1 - i) * 60), "v": round(cur, 1)})
        return vals

    series = {
        "cpu": walk(45.0, 6.0, 3.0, 97.0),
        "mem": walk(68.0, 1.5, 20.0, 95.0),
        "disk": walk(38.0, 0.1, 5.0, 98.0),
        "load": walk(1.2, 0.3, 0.0, 16.0),
    }
    latest = {k: (v[-1]["v"] if v else None) for k, v in series.items()}
    return {"mapped": True, "real": False, "asset_id": asset_id, "minutes": minutes, "latest": latest, "series": series}


# ===== 采集落库 =====


def _aligned_ts(dt: datetime | None = None) -> datetime:
    """对齐到 5 分钟槽，保证重复采集幂等（同一槽只留一帧）。"""
    dt = dt or utcnow()
    return dt.replace(second=0, microsecond=0) - timedelta(minutes=dt.minute % 5)


def _latest_values(latest: dict) -> dict:
    """兼容 real（load1）与 mock（load）两种最新值键名。"""
    return {
        "cpu": latest.get("cpu"),
        "mem": latest.get("mem"),
        "disk": latest.get("disk"),
        "load1": latest.get("load1", latest.get("load")),
    }


def collect_asset(db: Session, asset: Asset) -> str:
    """采集单资产一帧指标并落库。返回 stored（已落库）/ unmapped（未接入且 mock）。"""
    latest: dict = {}
    source = "real"
    client = resolve_real_client(db, asset)
    if client is not None:
        try:
            data = client.asset_metrics(asset.id, host_hint_of(asset), 10)
            if data.get("mapped") and data.get("latest"):
                latest = data["latest"]
        except Exception:  # noqa: BLE001  采集失败不阻断整体轮次
            latest = {}
    if not latest:
        latest = mock_asset_metrics(asset.id, 10)["latest"]
        source = "mock"

    vals = _latest_values(latest)
    ts = _aligned_ts()
    row = db.query(MetricSample).filter(MetricSample.asset_id == asset.id, MetricSample.ts == ts).one_or_none()
    if row is None:
        row = MetricSample(asset_id=asset.id, ts=ts, source=source)
        db.add(row)
    row.cpu = vals["cpu"]
    row.mem = vals["mem"]
    row.disk = vals["disk"]
    row.load1 = vals["load1"]
    row.source = source
    return "stored"


def run_collect_cycle(db: Session) -> dict:
    """采集全部资产一轮（celery beat 每 5 分钟调用）。单资产失败不阻断整体。"""
    stored = failed = 0
    assets = db.scalars(select(Asset)).all()
    for a in assets:
        try:
            if collect_asset(db, a) == "stored":
                stored += 1
        except Exception:  # noqa: BLE001
            db.rollback()
            failed += 1
    db.commit()
    return {"stored": stored, "failed": failed, "total": len(assets)}


# ===== 序列查询 =====


def _ts_unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _bucket_seconds(hours: float) -> int:
    return max(60, -(-int(hours * 3600) // _MAX_POINTS // 60) * 60)


def _downsample(rows: list[MetricSample], bucket: int, start: datetime) -> dict[str, list[dict]]:
    """按 bucket 秒分桶取均值，输出 {cpu: [{t, v}], ...}（t 为桶起点 unix 秒）。"""
    acc: dict[int, dict[str, list[float]]] = {}
    for r in rows:
        slot = (_ts_unix(r.ts) - _ts_unix(start)) // bucket * bucket
        d = acc.setdefault(slot, {})
        for k in METRIC_KEYS:
            v = getattr(r, _db_col(k))
            if v is not None:
                d.setdefault(k, []).append(v)
    out: dict[str, list[dict]] = {k: [] for k in METRIC_KEYS}
    base_unix = _ts_unix(start)
    for slot in sorted(acc):
        t = base_unix + slot
        for k in METRIC_KEYS:
            vals = acc[slot].get(k)
            if vals:
                out[k].append({"t": t, "v": round(sum(vals) / len(vals), 2)})
    return out


def query_series(db: Session, asset_id: str, hours: float, end: datetime | None = None) -> dict:
    """本地库查询时间窗口序列（降采样）+ 最新值。"""
    end = end or utcnow()
    start = end - timedelta(hours=hours)
    rows = db.scalars(
        select(MetricSample)
        .where(MetricSample.asset_id == asset_id, MetricSample.ts >= start, MetricSample.ts <= end)
        .order_by(MetricSample.ts)
    ).all()
    series = _downsample(rows, _bucket_seconds(hours), start)
    latest = {k: (s[-1]["v"] if s else None) for k, s in series.items()}
    return {"series": series, "latest": latest, "count": len(rows), "start": start, "end": end}


def query_compare(db: Session, asset_id: str, days: int, hours: float, end: datetime | None = None) -> list[dict]:
    """多日对比：取过去 N 天每天同长度窗口，平移对齐到当前时间轴（便于同轴叠加）。"""
    end = end or utcnow()
    bucket = _bucket_seconds(hours)
    out = []
    for d in range(1, days + 1):
        day_end = end - timedelta(days=d)
        start = day_end - timedelta(hours=hours)
        rows = db.scalars(
            select(MetricSample)
            .where(MetricSample.asset_id == asset_id, MetricSample.ts >= start, MetricSample.ts <= day_end)
            .order_by(MetricSample.ts)
        ).all()
        shifted: dict[str, list[dict]] = {k: [] for k in METRIC_KEYS}
        for k, pts in _downsample(rows, bucket, start).items():
            shifted[k] = [{"t": p["t"] + d * 86400, "v": p["v"]} for p in pts]
        out.append({"date": day_end.strftime("%Y-%m-%d"), "offset_days": d, "series": shifted})
    return out


# ===== 历史基线 / 异常检测 =====

_BASELINE_DAYS = 7
_SLOT_SECONDS = 900  # 基线按一天内的 15 分钟时段分桶（96 槽）


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = (len(sorted_vals) - 1) * p
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def build_baseline(db: Session, asset_id: str, days: int = _BASELINE_DAYS, end: datetime | None = None) -> dict:
    """历史基线：过去 N 天（不含当前窗口）样本按日内 15 分钟时段建 P05~P95 正常带。

    时段内样本不足 3 个时回落到全天全局分位，避免冷启动时段误报。
    返回 {metric: {"slots": [(slot, low, high)], "global": [low, high]}}。
    """
    end = end or utcnow()
    today0 = end.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today0 - timedelta(days=days)
    rows = db.scalars(
        select(MetricSample).where(MetricSample.asset_id == asset_id, MetricSample.ts >= start, MetricSample.ts < today0)
    ).all()
    buckets: dict[str, dict[int, list[float]]] = {k: {} for k in METRIC_KEYS}
    for r in rows:
        slot = int((_ts_unix(r.ts) % 86400) // _SLOT_SECONDS)
        for k in METRIC_KEYS:
            v = getattr(r, _db_col(k))
            if v is not None:
                buckets[k].setdefault(slot, []).append(v)
    baseline: dict[str, dict] = {}
    for k in METRIC_KEYS:
        slots = buckets[k]
        all_vals = sorted(v for vals in slots.values() for v in vals)
        g = [_percentile(all_vals, 0.05), _percentile(all_vals, 0.95)] if len(all_vals) >= 5 else None
        slot_bands: dict[int, tuple[float, float]] = {}
        for slot, vals in slots.items():
            if len(vals) >= 3:
                sv = sorted(vals)
                slot_bands[slot] = (_percentile(sv, 0.05), _percentile(sv, 0.95))
            elif g:
                slot_bands[slot] = (g[0], g[1])
        baseline[k] = {"slots": slot_bands, "global": g}
    return baseline


def detect_anomalies(series: dict[str, list[dict]], baseline: dict) -> list[dict]:
    """异常检测：当前序列点显著偏离历史基线（且连续 ≥2 点，滤单点噪声）。

    返回 [{t, metric, value, low, high}]；无可比基线（历史不足）时返回空。
    """
    anomalies: list[dict] = []
    for metric, pts in (series or {}).items():
        entry = baseline.get(metric) or {}
        if not (entry.get("slots") or entry.get("global")):
            continue
        run: list[dict] = []
        for p in pts:
            band = _band_at_baseline(entry, p["t"])
            if band is None:
                run = []
                continue
            low, high = band
            pad = max(2.0, (high - low) * 0.05)
            if p["v"] > high + pad or p["v"] < low - pad:
                run.append({"t": p["t"], "metric": metric, "value": p["v"], "low": round(low, 2), "high": round(high, 2)})
            else:
                if len(run) >= 2:
                    anomalies.extend(run)
                run = []
        if len(run) >= 2:
            anomalies.extend(run)
    return anomalies


def _band_at_baseline(entry: dict, t_unix: int) -> tuple[float, float] | None:
    """按时间槽取基线带（不依赖 today0，槽位置只与日内时刻有关）。"""
    slot = int((t_unix % 86400) // _SLOT_SECONDS)
    band = (entry.get("slots") or {}).get(slot)
    if band is None:
        band = entry.get("global")
    return (band[0], band[1]) if band else None


def baseline_bands(baseline: dict, start_unix: int, end_unix: int) -> dict[str, list[dict]]:
    """把基线带展开为时间轴上的分段（前端 markArea / 阶梯带渲染用）。"""
    out: dict[str, list[dict]] = {}
    for metric in METRIC_KEYS:
        entry = baseline.get(metric) or {}
        if not (entry.get("slots") or entry.get("global")):
            continue
        segs: list[dict] = []
        cur: dict | None = None
        for t in range(start_unix - start_unix % _SLOT_SECONDS, end_unix + 1, _SLOT_SECONDS):
            band = _band_at_baseline(entry, t)
            if band is None:
                cur = None
                continue
            low, high = round(band[0], 2), round(band[1], 2)
            if cur and cur["low"] == low and cur["high"] == high:
                cur["to"] = t + _SLOT_SECONDS
            else:
                cur = {"from": t, "to": t + _SLOT_SECONDS, "low": low, "high": high}
                segs.append(cur)
        out[metric] = segs
    return out


# ===== 趋势预测 =====


def forecast_series(points: list[dict], horizon_minutes: int = 60, steps: int = 12) -> dict:
    """线性回归（OLS）外推未来趋势。

    取最近 ≤2 小时历史点拟合，斜率过小（相对噪声）时给水平预测；
    cpu/mem/disk 限幅 0~100。返回 {"points": [{t, v}], "slope_per_hour": f, "method": str}。
    """
    if not points:
        return {"points": [], "slope_per_hour": 0.0, "method": "none"}
    tail = points[-min(len(points), 24):]  # ≤2h @5min 粒度
    xs = [p["t"] for p in tail]
    ys = [p["v"] for p in tail]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx > 0 else 0.0
    intercept = my - slope * mx
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    noise = (sum(r * r for r in residuals) / max(n - 2, 1)) ** 0.5 if n > 2 else 0.0
    # 斜率不足以越过噪声水平时，给水平预测（最后均值），避免把噪声放大成趋势
    if abs(slope) * 3600 < max(noise, 0.5):
        slope, intercept = 0.0, my
        method = "flat"
    else:
        method = "ols-linear"
    step = max(int(horizon_minutes * 60 / steps), 60)
    last_t = xs[-1]
    pts = []
    for i in range(1, steps + 1):
        t = last_t + i * step
        v = slope * t + intercept
        pts.append({"t": t, "v": round(v, 2)})
    return {"points": pts, "slope_per_hour": round(slope * 3600, 4), "method": method}
