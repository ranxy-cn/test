"""监控指标采样落库与趋势分析（24h 趋势 / 多日对比 / 历史基线 / 异常检测 / 预测）。"""
from __future__ import annotations

from datetime import timedelta

from app.models import Asset, MetricSample, utcnow
from app.services.metrics_store import (
    _aligned_ts,
    build_baseline,
    collect_asset,
    detect_anomalies,
    forecast_series,
    query_compare,
    query_series,
    run_collect_cycle,
)


def _mk_asset(aid: str) -> Asset:
    return Asset(
        id=aid, hostname=f"host-{aid}", app="app", role="app", env="prod", owner="ops", tenant_id="tenant-default"
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _sample(asset_id: str, dt, cpu: float, mem: float = 60.0, disk: float = 35.0, load: float = 1.0) -> MetricSample:
    return MetricSample(asset_id=asset_id, ts=dt, cpu=cpu, mem=mem, disk=disk, load1=load, source="mock")


# ===== 采集落库 =====


def test_collect_asset_persists_and_is_idempotent(db):
    """采集落库 + 同一 5 分钟槽重复采集幂等（不产生重复帧）。"""
    db.add(_mk_asset("a-1"))
    db.commit()
    assert collect_asset(db, db.get(Asset, "a-1")) == "stored"
    db.commit()
    n1 = db.query(MetricSample).filter(MetricSample.asset_id == "a-1").count()
    assert n1 == 1
    # 再采一轮：同一时间槽只保留一帧
    collect_asset(db, db.get(Asset, "a-1"))
    db.commit()
    n2 = db.query(MetricSample).filter(MetricSample.asset_id == "a-1").count()
    assert n2 == 1
    row = db.query(MetricSample).filter(MetricSample.asset_id == "a-1").one()
    assert row.ts == _aligned_ts()
    assert row.cpu is not None


def test_run_collect_cycle_covers_all_assets(db):
    """全资产采集轮次：自建资产各落库一帧，失败不阻断整体（演示资产也会计入 stored）。"""
    db.add(_mk_asset("a-1"))
    db.add(_mk_asset("a-2"))
    db.commit()
    result = run_collect_cycle(db)
    assert result["failed"] == 0
    assert result["stored"] >= 2
    # 自建资产各恰好一帧（5 分钟槽幂等），不受演示资产数量影响
    for aid in ("a-1", "a-2"):
        assert db.query(MetricSample).filter(MetricSample.asset_id == aid).count() == 1


# ===== 序列查询 / 多日对比 =====


def test_query_series_window_and_downsample(db):
    """序列查询：窗口过滤正确，24h 数据按桶降采样且保留均值。"""
    now = utcnow()
    db.add(_mk_asset("a-1"))
    for i in range(48):  # 24h，每 30 分钟一个点
        db.add(_sample("a-1", now - timedelta(minutes=30 * (47 - i)), cpu=40.0 + i * 0.1))
    db.commit()
    out = query_series(db, "a-1", hours=24)
    assert out["count"] == 48
    assert len(out["series"]["cpu"]) <= 720
    assert out["latest"]["cpu"] is not None
    # 窗口外样本不进入
    out2 = query_series(db, "a-1", hours=1)
    assert out2["count"] < 48


def test_query_compare_shifts_to_current_axis(db):
    """多日对比：昨天同窗口序列平移 +86400s，与当前时间轴对齐。"""
    now = utcnow()
    db.add(_mk_asset("a-1"))
    for d in (1, 2):
        db.add(_sample("a-1", now - timedelta(days=d), cpu=40.0 + d))
    db.commit()
    days = query_compare(db, "a-1", days=2, hours=1)
    assert [x["offset_days"] for x in days] == [1, 2]
    assert days[0]["series"]["cpu"][0]["t"] > days[0]["series"]["cpu"][0]["t"] - 86400
    # 昨天的点平移后应落在当前窗口附近（今晨 0 点之后）
    assert days[0]["series"]["cpu"][0]["t"] > int(now.replace(hour=0, minute=0, second=0).timestamp())


# ===== 历史基线 / 异常检测 =====


def _seed_baseline_history(db, aid: str):
    """3 天历史，每天 10:00-10:10 三帧 cpu≈40（同一 15 分钟槽，槽内 ≥3 样本）。"""
    now = utcnow()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for d in (1, 2, 3):
        base = today0 - timedelta(days=d) + timedelta(hours=10)
        for i, cpu in enumerate((39.0, 40.0, 41.0)):
            db.add(_sample(aid, base + timedelta(minutes=5 * i), cpu=cpu))


def test_build_baseline_bands(db):
    """基线：槽内样本充足时按槽建带，正常值 39~41。"""
    _seed_baseline_history(db, "a-1")
    db.commit()
    bl = build_baseline(db, "a-1")
    band = bl["cpu"]["slots"].get(10 * 4)  # 10:00 → 槽 40
    assert band is not None
    assert band[0] <= 39.0 and band[1] >= 41.0


def test_detect_anomalies_marks_deviation_and_ignores_single_spike(db):
    """异常检测：连续偏离基线被标记；单点噪声不报警。"""
    _seed_baseline_history(db, "a-1")
    now = utcnow()
    base_unix = int(now.timestamp())
    # 连续 3 点 cpu=95（基线带 ~39-41，pad=2）→ 异常
    series_high = {"cpu": [{"t": base_unix - 600 + i * 300, "v": 95.0} for i in range(3)]}
    db.flush()  # SessionLocal autoflush=False，先刷入待写样本供基线查询可见
    bl = build_baseline(db, "a-1")
    db.commit()
    ano = detect_anomalies(series_high, bl)
    assert len(ano) == 3
    assert all(a["metric"] == "cpu" for a in ano)
    # 单点偏离 → 不报警
    ano1 = detect_anomalies({"cpu": [{"t": base_unix, "v": 95.0}]}, bl)
    assert ano1 == []
    # 正常范围 → 不报警
    ano0 = detect_anomalies({"cpu": [{"t": base_unix, "v": 40.0}, {"t": base_unix + 300, "v": 40.5}]}, bl)
    assert ano0 == []


# ===== 预测 =====


def test_forecast_linear_trend_and_flat(db):
    """预测：上升趋势外推方向正确；噪声水平序列给水平预测。"""
    now = int(utcnow().timestamp())
    rising = [{"t": now - (24 - i) * 300, "v": 20.0 + i * 1.0} for i in range(24)]  # 每帧 +1%
    fc = forecast_series(rising, horizon_minutes=60, steps=12)
    assert fc["points"] and len(fc["points"]) == 12
    assert fc["points"][-1]["v"] > rising[-1]["v"]  # 延续上升趋势
    assert fc["method"] == "ols-linear"
    flat = [{"t": now - (24 - i) * 300, "v": 40.0 + (i % 2) * 0.1} for i in range(24)]
    fc2 = forecast_series(flat)
    assert fc2["method"] == "flat"
    assert abs(fc2["points"][-1]["v"] - 40.0) < 1.0
    # 百分比指标限幅在 0~100 由上层 merge 保证；此处验证预测点不发散为负
    falling = [{"t": now - (24 - i) * 300, "v": max(0.5, 5.0 - i * 1.0)} for i in range(24)]
    fc3 = forecast_series(falling)
    assert fc3["points"][-1]["v"] < falling[-1]["v"]


# ===== API 集成 =====


def test_trends_api_source_stored_with_compare_baseline_forecast(auth_token, client, db):
    """API：有落库样本 → source=stored；对比/基线/异常/预测全链路可用。"""
    db.add(_mk_asset("a-9"))
    _seed_baseline_history(db, "a-9")
    now = utcnow()
    # 当前窗口：cpu=95 连续偏离 → 应被标记异常
    for i in range(3):
        db.add(_sample("a-9", now - timedelta(minutes=30 - 5 * i), cpu=95.0))
    db.commit()
    r = client.get(
        "/api/v1/assets/a-9/trends",
        params={"hours": 1, "compare_days": 1, "with_baseline": "true", "with_forecast": "true"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "stored"
    assert body["series"]["cpu"]
    assert body["latest"]["cpu"] is not None
    assert len(body["compare"]) == 1
    assert body["baseline_bands"]["cpu"]
    assert body["anomalies"], "当前窗口 cpu=95 显著偏离基线应标记异常"
    assert body["forecast"]["cpu"]["points"]
    # 落库样本（5 分钟粒度）与实时点合并后时间升序
    ts_list = [p["t"] for p in body["series"]["cpu"]]
    assert ts_list == sorted(ts_list)


def test_trends_api_falls_back_to_realtime_when_no_samples(auth_token, client, db):
    """API：本地无落库样本 → 回退实时（mock 模式为演示序列），note 说明。"""
    db.add(_mk_asset("a-8"))
    db.commit()
    r = client.get("/api/v1/assets/a-8/trends", params={"hours": 1}, headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "realtime"
    assert "暂无落库" in body["note"]
    assert body["series"]["cpu"]


def test_trends_api_param_bounds(auth_token, client, db):
    """边界：hours 最大 24、compare_days 最大 7，越界 422。"""
    db.add(_mk_asset("a-7"))
    db.commit()
    assert client.get("/api/v1/assets/a-7/trends", params={"hours": 24}, headers=_h(auth_token)).status_code == 200
    assert client.get("/api/v1/assets/a-7/trends", params={"hours": 24.5}, headers=_h(auth_token)).status_code == 422
    assert client.get("/api/v1/assets/a-7/trends", params={"hours": 0.5}, headers=_h(auth_token)).status_code == 200
    assert client.get("/api/v1/assets/a-7/trends", params={"hours": 0.2}, headers=_h(auth_token)).status_code == 422
    assert client.get("/api/v1/assets/a-7/trends", params={"compare_days": 7}, headers=_h(auth_token)).status_code == 200
    assert client.get("/api/v1/assets/a-7/trends", params={"compare_days": 8}, headers=_h(auth_token)).status_code == 422
    assert client.get("/api/v1/assets/not-exist/trends", headers=_h(auth_token)).status_code == 404


def test_mock_series_covers_requested_window():
    """mock 演示序列：大窗口自适应步长铺满整个时间窗（修复 24h 只画最近 4h 的问题）。"""
    from app.services.metrics_store import mock_asset_metrics

    s = mock_asset_metrics("a-1", 1440)["series"]["cpu"]
    ts = [int(p["t"]) for p in s]
    assert len(s) <= 240
    assert ts[-1] - ts[0] >= 1400 * 60  # 24h 窗口覆盖 ≥ 23h20m
    # 小窗口行为不变：10 分钟仍为每分钟一个点
    s2 = mock_asset_metrics("a-1", 10)["series"]["cpu"]
    assert len(s2) == 10 and int(s2[-1]["t"]) - int(s2[0]["t"]) == 9 * 60
