"""系统资源实时监控：/proc 读取器、差值速率、采样落库、保留清理、realtime/history 端点。"""
from __future__ import annotations

import types
from datetime import timedelta

import pytest

from app.models import SystemMetricSample, utcnow
from app.services import system_live

STAT_V1 = "cpu  100 0 200 1000 0 0 0 0 0 0\n"
STAT_V2 = "cpu  150 0 250 1050 0 0 0 0 0 0\n"
MEMINFO = "MemTotal:        2048000 kB\nMemAvailable:     512000 kB\nBuffers:          1024 kB\n"
LOADAVG = "0.50 0.40 0.30 1/100 1234\n"
NETDEV_V1 = (
    "inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    "    eth0: 1000 10 0 0 0 0 0 0 500 5 0 0 0 0 0 0\n"
    "      lo: 999999 100 0 0 0 0 0 0 999999 100 0 0 0 0 0 0\n"
)
NETDEV_V2 = NETDEV_V1.replace("eth0: 1000 10", "eth0: 6000 60").replace(" 500 5 0", " 1500 15 0")
ROUTE = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\tC0A80101\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "wlan0\t00000000\tC0A80101\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
)


@pytest.fixture
def fakeroot(tmp_path, monkeypatch):
    """伪造 /proc 目录（stat/meminfo/loadavg/net/dev/net/route）+ 受控时钟。"""
    root = tmp_path / "proc"
    (root / "net").mkdir(parents=True)
    (root / "stat").write_text(STAT_V1)
    (root / "meminfo").write_text(MEMINFO)
    (root / "loadavg").write_text(LOADAVG)
    (root / "net" / "dev").write_text(NETDEV_V1)
    (root / "net" / "route").write_text(ROUTE)
    monkeypatch.setattr(system_live, "PROC_ROOT_OVERRIDE", str(root))
    monkeypatch.setattr(system_live, "_prev", None)
    # 整体替换模块内 time 引用（SimpleNamespace），避免污染全局 time 模块
    clock = {"t": 1000.0}
    monkeypatch.setattr(system_live, "time", types.SimpleNamespace(time=lambda: clock["t"]))
    return root, clock


def _advance(root, clock, dt: float = 5.0):
    """推进时钟并更新 /proc 内容：cpu jiffies +100（其中 idle +50），eth0 rx +5000B tx +1000B。"""
    (root / "stat").write_text(STAT_V2)
    (root / "net" / "dev").write_text(NETDEV_V2)
    clock["t"] += dt


# ===== /proc 快照与差值速率 =====


def test_read_snapshot_first_call_no_rates(fakeroot):
    """首帧：无上一次快照，CPU% 与网速为 None；内存/负载/网卡直接可读。"""
    s = system_live.read_snapshot()
    assert s["supported"] is True
    assert s["note"] == ""
    assert s["cpu"] is None
    assert s["net_rx_bps"] is None and s["net_tx_bps"] is None
    assert s["mem"] == 75.0  # (2048000-512000)/2048000
    assert s["mem_used_mb"] == 1500 and s["mem_total_mb"] == 2000
    assert (s["load1"], s["load5"], s["load15"]) == (0.5, 0.4, 0.3)
    assert s["net_iface"] == "eth0"  # /proc/net/route 默认路由


def test_read_snapshot_delta_rates(fakeroot):
    """第二帧：CPU% 与网速为相邻快照差值（与采样间隔无关）。"""
    root, clock = fakeroot
    system_live.read_snapshot()
    _advance(root, clock)  # +5s
    s2 = system_live.read_snapshot()
    # d_total=150, d_idle=50 → (1 - 50/150) * 100
    assert s2["cpu"] == 66.67
    assert s2["net_rx_bps"] == 1000.0  # 5000B / 5s
    assert s2["net_tx_bps"] == 200.0  # 1000B / 5s


def test_primary_iface_falls_back_to_busiest(fakeroot):
    """无默认路由：排除 lo 等虚拟网卡后取流量最大的接口。"""
    root, _ = fakeroot
    (root / "net" / "route").unlink()
    assert system_live._primary_iface(str(root)) == "eth0"


def test_proc_root_unsupported(tmp_path, monkeypatch):
    """/proc 不可用：supported=False，note 说明，不抛异常。"""
    monkeypatch.setattr(system_live, "PROC_ROOT_OVERRIDE", str(tmp_path / "nope"))
    assert system_live.proc_root() == ""
    s = system_live.read_snapshot()
    assert s["supported"] is False
    assert "无法实时采集" in s["note"]


# ===== 采样落库 / 保留清理 =====


def test_persist_sample_writes_row(fakeroot, db):
    row = system_live.persist_sample(db)
    db.commit()
    assert row is not None and row.source == "real"
    assert row.mem == 75.0 and row.load1 == 0.5
    assert db.query(SystemMetricSample).count() == 1


def test_persist_sample_skips_when_unsupported(tmp_path, monkeypatch, db):
    """不支持的环境跳过落库，保证不写全空数据。"""
    monkeypatch.setattr(system_live, "PROC_ROOT_OVERRIDE", str(tmp_path / "nope"))
    assert system_live.persist_sample(db) is None
    assert db.query(SystemMetricSample).count() == 0


def test_cleanup_old_deletes_expired(db):
    now = utcnow()
    db.add(SystemMetricSample(ts=now - timedelta(days=8), source="real"))
    db.add(SystemMetricSample(ts=now - timedelta(days=1), source="real"))
    db.commit()
    assert system_live.cleanup_old(db, days=7) == 1
    assert db.query(SystemMetricSample).count() == 1


# ===== API 集成 =====


def test_realtime_and_history_api(auth_token, client, db, fakeroot):
    """realtime：首帧无速率、第二帧有；history：窗口过滤正确。"""
    root, clock = fakeroot
    r1 = client.get("/api/v1/system/metrics/realtime")
    assert r1.status_code == 200
    assert r1.json()["supported"] is True
    _advance(root, clock)
    r2 = client.get("/api/v1/system/metrics/realtime").json()
    assert r2["cpu"] == 66.67 and r2["net_iface"] == "eth0"

    now = utcnow()
    db.add(SystemMetricSample(ts=now - timedelta(minutes=2), cpu=10.0, source="real"))
    db.add(SystemMetricSample(ts=now - timedelta(hours=2), cpu=99.0, source="real"))
    db.commit()
    body = client.get("/api/v1/system/metrics/history", params={"minutes": 60}).json()
    assert body["count"] >= 1
    assert all(item["cpu"] != 99.0 for item in body["items"])  # 2h 前的行不在 60min 窗口
    assert body["items"] == sorted(body["items"], key=lambda x: x["ts"])  # 时间升序


def test_history_api_param_bounds(auth_token, client):
    """minutes 边界：1~86400（2 个月），越界 422。"""
    assert client.get("/api/v1/system/metrics/history", params={"minutes": 1}).status_code == 200
    assert client.get("/api/v1/system/metrics/history", params={"minutes": 86400}).status_code == 200
    assert client.get("/api/v1/system/metrics/history", params={"minutes": 86401}).status_code == 422
    assert client.get("/api/v1/system/metrics/history", params={"minutes": 0.5}).status_code == 422


def test_history_default_retention_is_60_days(db):
    """保留期默认 2 个月：cleanup_old 不传天数时按 60 天清理。"""
    from app.config import get_settings

    assert get_settings().system_sample_retention_days == 60
    now = utcnow()
    db.add(SystemMetricSample(ts=now - timedelta(days=61), source="real"))
    db.add(SystemMetricSample(ts=now - timedelta(days=59), source="real"))
    db.commit()
    assert system_live.cleanup_old(db) == 1
    assert db.query(SystemMetricSample).count() == 1


def test_history_api_bucket_aggregation(auth_token, client, db):
    """长窗口自动分桶：桶内 avg/max 聚合；≤2h 窗口保持原始粒度。"""
    from datetime import datetime, timezone

    now = utcnow()
    h0 = int(now.timestamp()) - int(now.timestamp()) % 3600  # 当前小时起点（同桶确定性）
    db.add(SystemMetricSample(ts=datetime.fromtimestamp(h0 + 60, tz=timezone.utc), cpu=10.0, mem=50.0, source="real"))
    db.add(SystemMetricSample(ts=datetime.fromtimestamp(h0 + 120, tz=timezone.utc), cpu=30.0, mem=70.0, source="real"))
    db.commit()
    # 3 天窗口 → 1 小时桶（count 为聚合后点数）
    body = client.get("/api/v1/system/metrics/history", params={"minutes": 4320}).json()
    assert body["bucket_seconds"] == 3600
    assert body["count"] == 1
    it = body["items"][-1]
    assert it["cpu"] == 20.0 and it["cpu_max"] == 30.0
    assert it["mem"] == 60.0 and it["mem_max"] == 70.0
    assert it["cpu"] <= it["cpu_max"]
    # 1 小时窗口 → 原始粒度，*_max 与均值相同
    body2 = client.get("/api/v1/system/metrics/history", params={"minutes": 60}).json()
    assert body2["bucket_seconds"] == 0
    assert body2["count"] == 2
    it2 = body2["items"][0]
    assert it2["cpu"] == it2["cpu_max"] == 10.0
