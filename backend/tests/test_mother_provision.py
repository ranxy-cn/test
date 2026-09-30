"""新增母机：SSH 验证 + 自动纳管本机子机 + 在线/监控数据由本机子机推导。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import Asset, SystemMetricSample, utcnow
from app.services import provision as provision_svc


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_mod():
    from app import api

    return api


def test_ssh_test_ok(auth_token, client, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "_verify_ssh", lambda ip, port, user, pwd: 128)
    r = client.post(
        "/api/v1/assets/ssh-test",
        json={"ip": "10.6.6.6", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["latency_ms"] == 128


def test_ssh_test_auth_fail(auth_token, client, api_mod, monkeypatch):
    def _fail(ip, port, user, pwd):
        raise provision_svc.ProvisionError("SSH 登录被拒绝：用户名或密码错误")

    monkeypatch.setattr(api_mod, "_verify_ssh", _fail)
    r = client.post(
        "/api/v1/assets/ssh-test",
        json={"ip": "10.6.6.6", "password": "bad"},
        headers=_h(auth_token),
    )
    assert r.status_code == 400
    assert "SSH 登录被拒绝" in r.json()["detail"]


def test_ssh_test_requires_password(auth_token, client):
    r = client.post("/api/v1/assets/ssh-test", json={"ip": "10.6.6.6"}, headers=_h(auth_token))
    assert r.status_code == 422  # 密码必填


def test_create_mother_auto_provision(auth_token, client, db, api_mod, monkeypatch):
    """带密码新增母机：SSH 验证通过 → 建母机 + 自动纳管本机子机（归属母机、签发 token、异步部署）。"""
    deployed = {}
    monkeypatch.setattr(api_mod, "_verify_ssh", lambda ip, port, user, pwd: 96)
    monkeypatch.setattr(
        api_mod,
        "_spawn_agent_deploy",
        lambda aid, **kw: deployed.update({"aid": aid, **kw}),
    )
    r = client.post(
        "/api/v1/assets/mothers",
        json={
            "hostname": "ops-m-new",
            "ip": "10.7.7.7",
            "ssh_port": 22,
            "username": "root",
            "password": "secret",
            "owner": "李四",
        },
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ssh_verified"] is True
    assert body["provision_started"] is True

    mother = db.get(Asset, "mother-10-7-7-7")
    assert mother is not None
    child_id = mother.extra["self_child_id"]
    assert child_id == "node-10-7-7-7-22"
    assert body["self_child_id"] == child_id

    child = db.get(Asset, child_id)
    assert child is not None and child.kind == "child"
    assert child.mother_id == mother.id
    assert (child.extra or {}).get("agent_token")  # 已签发上报令牌
    assert deployed["aid"] == child_id  # 自动部署已排队


def test_create_mother_ssh_fail_creates_nothing(auth_token, client, db, api_mod, monkeypatch):
    """SSH 验证失败 → 400，且不留下母机/子机死台账。"""

    def _fail(ip, port, user, pwd):
        raise provision_svc.ProvisionError("SSH 连接失败：无法连到 10.8.8.8")

    monkeypatch.setattr(api_mod, "_verify_ssh", _fail)
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-bad", "ip": "10.8.8.8", "password": "x"},
        headers=_h(auth_token),
    )
    assert r.status_code == 400
    assert "SSH 验证失败" in r.json()["detail"]
    assert db.get(Asset, "mother-10-8-8-8") is None
    assert db.get(Asset, "node-10-8-8-8-22") is None


def test_list_mothers_reachable_from_self_child(auth_token, client, db):
    """母机在线 = 本机子机 agent 在线（last_seen 新鲜→在线，过期→离线）。"""
    db.add(
        Asset(
            id="mother-10-9-9-9",
            hostname="m-ctx",
            kind="mother",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"self_child_id": "node-10-9-9-9-22", "provision": {"ip": "10.9.9.9", "port": 22}},
        )
    )
    child = Asset(
        id="node-10-9-9-9-22",
        hostname="10.9.9.9:22",
        kind="child",
        app="",
        role="other",
        env="prod",
        owner="",
        mother_id="mother-10-9-9-9",
        tenant_id="t1",
        extra={"agent_token": "t", "agent": {"last_seen": utcnow().isoformat()}},
    )
    db.add(child)
    db.commit()
    from app.routers.agent_api import agent_cfg_of

    offline_after = agent_cfg_of(child, db)["offline_after"]
    stale = (utcnow() - timedelta(seconds=offline_after * 10)).isoformat()

    items = client.get("/api/v1/assets/mothers", headers=_h(auth_token)).json()["items"]
    row = next(x for x in items if x["id"] == "mother-10-9-9-9")
    assert row["reachable"] is True and row["self_child_id"] == "node-10-9-9-9-22"

    child.extra = {**child.extra, "agent": {"last_seen": stale}}
    db.commit()
    items = client.get("/api/v1/assets/mothers", headers=_h(auth_token)).json()["items"]
    row = next(x for x in items if x["id"] == "mother-10-9-9-9")
    assert row["reachable"] is False


def test_metrics_of_mother_use_self_child(auth_token, client, db, monkeypatch):
    """母机 realtime/history 数据源 = 本机子机 agent 上报，而非平台本机。"""
    from app.routers import agent_api

    db.add(
        Asset(
            id="mother-10-10-10-10",
            hostname="m-mx",
            kind="mother",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"self_child_id": "node-10-10-10-10-22", "agent_token": "mt"},
        )
    )
    db.add(
        Asset(
            id="node-10-10-10-10-22",
            hostname="10.10.10.10:22",
            kind="child",
            app="",
            role="other",
            env="prod",
            owner="",
            mother_id="mother-10-10-10-10",
            tenant_id="t1",
            extra={"agent_token": "ct", "agent": {"last_seen": utcnow().isoformat()}},
        )
    )
    db.commit()
    db.add(
        SystemMetricSample(
            asset_id="node-10-10-10-10-22",
            ts=utcnow() - timedelta(seconds=10),
            cpu=33.3,
            mem=44.4,
            disk=5.0,
            load1=0.1,
        )
    )
    db.commit()
    monkeypatch.setattr(
        agent_api, "LATEST", {"node-10-10-10-10-22": {"cpu": 33.3, "mem": 44.4, "ts": utcnow().isoformat()}}
    )

    rt = client.get(
        "/api/v1/system/metrics/realtime?asset_id=mother-10-10-10-10",
        headers=_h(auth_token),
    ).json()
    assert rt["supported"] is True and rt["cpu"] == 33.3

    hist = client.get(
        "/api/v1/system/metrics/history?minutes=5&asset_id=mother-10-10-10-10",
        headers=_h(auth_token),
    ).json()
    assert hist["count"] == 1 and hist["items"][0]["cpu"] == 33.3


def test_history_of_bare_mother_is_empty(auth_token, client, db):
    """无本机子机的存量母机：没有真实数据，历史曲线为空（不伪造平台本机数据）。"""
    db.add(
        Asset(
            id="mother-10-11-11-11",
            hostname="m-bare",
            kind="mother",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
            extra={"provision": {"ip": "10.11.11.11", "port": 22}},
        )
    )
    db.add(SystemMetricSample(asset_id=None, ts=utcnow(), cpu=88.0))  # 平台本机样本
    db.commit()
    hist = client.get(
        "/api/v1/system/metrics/history?minutes=5&asset_id=mother-10-11-11-11",
        headers=_h(auth_token),
    ).json()
    assert hist["count"] == 0 and hist["items"] == []


def test_children_realtime_batch(auth_token, client, db, monkeypatch):
    """分组卡片批量实时端点：返回母机名下全部子机的指标（含网络）与在线态。"""
    from app.routers import agent_api

    db.add(
        Asset(
            id="mother-10-12-12-12",
            hostname="m-rt",
            kind="mother",
            app="",
            role="app",
            env="prod",
            owner="",
            tenant_id="t1",
        )
    )
    db.add(
        Asset(
            id="node-10-12-12-12-22",
            hostname="10.12.12.12:22",
            kind="child",
            app="",
            role="other",
            env="prod",
            owner="",
            mother_id="mother-10-12-12-12",
            tenant_id="t1",
            extra={"agent_token": "t", "agent": {"last_seen": utcnow().isoformat()}},
        )
    )
    db.add(
        Asset(
            id="node-10-12-12-99-22",
            hostname="10.12.12.99:22",
            kind="child",
            app="",
            role="other",
            env="prod",
            owner="",
            mother_id="mother-other",
            tenant_id="t1",
        )
    )
    db.commit()
    monkeypatch.setattr(
        agent_api,
        "LATEST",
        {
            "node-10-12-12-12-22": {
                "cpu": 11.1,
                "mem": 22.2,
                "disk": 33.3,
                "load1": 0.5,
                "net_rx_bps": 2048.0,
                "net_tx_bps": 1024.0,
                "ts": utcnow().isoformat(),
            }
        },
    )

    r = client.get("/api/v1/assets/mothers/mother-10-12-12-12/children/realtime", headers=_h(auth_token))
    assert r.status_code == 200
    items = r.json()["items"]
    # 只含该母机名下子机；不属于该母机的子机不出现
    assert set(items) == {"node-10-12-12-12-22"}
    m = items["node-10-12-12-12-22"]
    assert m["cpu"] == 11.1 and m["load"] == 0.5
    assert m["net_rx_bps"] == 2048.0 and m["net_tx_bps"] == 1024.0
    assert m["online"] is True

    r404 = client.get("/api/v1/assets/mothers/no-such/children/realtime", headers=_h(auth_token))
    assert r404.status_code == 404
