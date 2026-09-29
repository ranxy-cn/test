"""自研子机 Agent 体系测试：令牌/上报/配置/状态/数据隔离。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import Asset, SystemMetricSample, utcnow
from app.routers.agent_api import DEFAULT_AGENT_CONFIG, LATEST, _LATEST_LOCK


def _child(db, asset_id: str = "ch-agent-01", extra: dict | None = None) -> Asset:
    a = Asset(
        id=asset_id,
        hostname=asset_id,  # hostname 唯一
        app="Nginx",
        role="app",
        owner="",
        kind="child",
        tenant_id="tenant-default",
        extra=extra or {},
    )
    db.add(a)
    db.commit()
    return a


def _mk_token(client, auth_token: str, asset_id: str) -> str:
    r = client.post(f"/api/v1/assets/{asset_id}/agent/token", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.status_code == 200
    return r.json()["token"]


def test_report_requires_token(client, db):
    _child(db)
    r = client.post("/api/v1/agent/report", json={"asset_id": "ch-agent-01", "samples": []})
    assert r.status_code == 401
    r = client.post(
        "/api/v1/agent/report",
        json={"asset_id": "ch-agent-01", "samples": []},
        headers={"X-Agent-Token": "wrong"},
    )
    assert r.status_code == 401


def test_report_persists_and_caches(client, auth_token, db):
    _child(db)
    token = _mk_token(client, auth_token, "ch-agent-01")
    now = utcnow().timestamp()
    body = {
        "asset_id": "ch-agent-01",
        "agent_version": "1.0.0-py",
        "samples": [{"ts": now, "cpu": 12.5, "mem": 40.0, "disk": 30.0, "load1": 0.5, "net_rx_bps": 100.0, "net_tx_bps": 50.0}],
    }
    r = client.post("/api/v1/agent/report", json=body, headers={"X-Agent-Token": token})
    assert r.status_code == 200
    assert r.json()["accepted"] == 1

    row = db.query(SystemMetricSample).filter(SystemMetricSample.asset_id == "ch-agent-01").one()
    assert row.cpu == 12.5 and row.source == "agent"
    with _LATEST_LOCK:
        assert LATEST["ch-agent-01"]["cpu"] == 12.5

    # 状态：刚上报 → 在线
    r = client.get("/api/v1/assets/ch-agent-01/agent/status", headers={"Authorization": f"Bearer {auth_token}"})
    st = r.json()
    assert st["online"] is True and st["version"] == "1.0.0-py"


def test_report_batch_backfill_and_cap(client, auth_token, db):
    _child(db)
    token = _mk_token(client, auth_token, "ch-agent-01")
    now = utcnow().timestamp()
    samples = [{"ts": now - 60 + i, "cpu": float(i)} for i in range(260)]  # 超 200 上限
    r = client.post(
        "/api/v1/agent/report",
        json={"asset_id": "ch-agent-01", "samples": samples},
        headers={"X-Agent-Token": token},
    )
    assert r.json()["accepted"] == 200
    assert db.query(SystemMetricSample).filter(SystemMetricSample.asset_id == "ch-agent-01").count() == 200


def test_agent_config_roundtrip(client, auth_token, db):
    _child(db)
    token = _mk_token(client, auth_token, "ch-agent-01")
    # agent 拉取 → 默认配置
    r = client.get("/api/v1/agent/config", params={"asset_id": "ch-agent-01"}, headers={"X-Agent-Token": token})
    assert r.json()["config"] == DEFAULT_AGENT_CONFIG
    # 管理端改配置
    r = client.put(
        "/api/v1/assets/ch-agent-01/agent/config",
        json={"report_interval": 10, "collect_items": ["cpu", "mem"]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert r.status_code == 200
    assert r.json()["config"]["report_interval"] == 10
    # agent 再拉 → 新配置
    r = client.get("/api/v1/agent/config", params={"asset_id": "ch-agent-01"}, headers={"X-Agent-Token": token})
    assert r.json()["config"]["report_interval"] == 10
    # 非法值收敛
    r = client.put(
        "/api/v1/assets/ch-agent-01/agent/config",
        json={"report_interval": 99999, "collect_items": ["hacker"]},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    cfg = r.json()["config"]
    assert cfg["report_interval"] == 86400
    assert set(cfg["collect_items"]) == set(DEFAULT_AGENT_CONFIG["collect_items"])


def test_offline_detection(client, auth_token, db):
    extra = {"agent": {"last_seen": (utcnow() - timedelta(seconds=120)).isoformat(), "version": "1.0.0-py"}}
    _child(db, extra=extra)
    _mk_token(client, auth_token, "ch-agent-01")
    r = client.get("/api/v1/assets/ch-agent-01/agent/status", headers={"Authorization": f"Bearer {auth_token}"})
    assert r.json()["online"] is False  # 120s > offline_after(30s)


def test_realtime_and_history_asset_isolation(client, auth_token, db):
    """两条子机数据互不混淆；母机（无参数）曲线不含子机样本。"""
    _child(db, "ch-a")
    _child(db, "ch-b")
    t1 = _mk_token(client, auth_token, "ch-a")
    t2 = _mk_token(client, auth_token, "ch-b")
    now = utcnow().timestamp()
    for aid, tk, cpu in (("ch-a", t1, 11.0), ("ch-b", t2, 77.0)):
        r = client.post(
            "/api/v1/agent/report",
            json={"asset_id": aid, "samples": [{"ts": now, "cpu": cpu, "mem": 50.0}]},
            headers={"X-Agent-Token": tk},
        )
        assert r.status_code == 200

    # realtime 按资产隔离
    h = {"Authorization": f"Bearer {auth_token}"}
    ra = client.get("/api/v1/system/metrics/realtime", params={"asset_id": "ch-a"}, headers=h).json()
    rb = client.get("/api/v1/system/metrics/realtime", params={"asset_id": "ch-b"}, headers=h).json()
    assert ra["cpu"] == 11.0 and rb["cpu"] == 77.0
    assert ra["online"] is True and rb["online"] is True

    # history：子机各自只含自己的样本
    ha = client.get("/api/v1/system/metrics/history", params={"asset_id": "ch-a", "minutes": 60}, headers=h).json()
    hb = client.get("/api/v1/system/metrics/history", params={"asset_id": "ch-b", "minutes": 60}, headers=h).json()
    assert ha["count"] == 1 and hb["count"] == 1
    assert ha["items"][0]["cpu"] == 11.0 and hb["items"][0]["cpu"] == 77.0

    # 母机（无 asset_id）→ 只含本机样本（asset_id IS NULL），排除子机数据
    hm = client.get("/api/v1/system/metrics/history", params={"minutes": 60}, headers=h).json()
    assert all(True for _ in hm["items"])  # 母机样本可能为 0（测试环境无采样线程）
    assert hm["count"] == 0


def test_report_unknown_asset(client, db):
    r = client.post(
        "/api/v1/agent/report",
        json={"asset_id": "ghost", "samples": []},
        headers={"X-Agent-Token": "x"},
    )
    assert r.status_code == 401


@pytest.fixture(autouse=True)
def _clear_latest():
    with _LATEST_LOCK:
        LATEST.clear()
    yield
    with _LATEST_LOCK:
        LATEST.clear()
