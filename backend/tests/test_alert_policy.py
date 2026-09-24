"""母机告警策略：默认值 / 校验 / 存储 / 绑定 Zabbix 时的模板同步（mock 下跳过同步）。"""
from __future__ import annotations

import pytest

from app.services.alert_policy import DEFAULT_POLICY, normalize_policy


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_normalize_policy_defaults_and_partial():
    p = normalize_policy(None)
    assert p == DEFAULT_POLICY
    p2 = normalize_policy({"cpu_threshold": 80, "cpu_window_minutes": 1})
    assert p2["cpu_threshold"] == 80 and p2["cpu_window_minutes"] == 1
    assert p2["mem_threshold"] == DEFAULT_POLICY["mem_threshold"]
    p3 = normalize_policy({"load_threshold": "2.5"})
    assert p3["load_threshold"] == 2.5


def test_normalize_policy_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_policy({"cpu_threshold": 0})
    with pytest.raises(ValueError):
        normalize_policy({"mem_threshold": 100})
    with pytest.raises(ValueError):
        normalize_policy({"cpu_window_minutes": 0})
    with pytest.raises(ValueError):
        normalize_policy({"load_window_minutes": 999})
    with pytest.raises(ValueError):
        normalize_policy({"load_threshold": "abc"})


def test_create_mother_stores_alert_policy(auth_token, client, db):
    r = client.post(
        "/api/v1/assets/mothers",
        json={
            "hostname": "ops-zbx-pol",
            "ip": "10.1.0.8",
            "ack_risk": True,
            "alert_policy": {"cpu_threshold": 85, "cpu_window_minutes": 2},
        },
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    # 未传字段补默认值（通过策略查询接口验证存储值）
    g = client.get("/api/v1/assets/mothers/mother-10-1-0-8/alert-policy", headers=_h(auth_token))
    assert g.status_code == 200
    assert g.json()["policy"]["cpu_threshold"] == 85
    assert g.json()["policy"]["cpu_window_minutes"] == 2
    assert g.json()["policy"]["mem_threshold"] == DEFAULT_POLICY["mem_threshold"]

    r2 = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-zbx-bad", "ip": "10.1.0.9", "ack_risk": True, "alert_policy": {"cpu_threshold": 0}},
        headers=_h(auth_token),
    )
    assert r2.status_code == 400


def test_get_policy_defaults_for_mother_without_policy(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-pol", hostname="mo-pol", app="Zabbix Server", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.get("/api/v1/assets/mothers/mo-pol/alert-policy", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["policy"] == DEFAULT_POLICY
    assert body["defaults"] == DEFAULT_POLICY
    assert body["has_zabbix"] is False
    assert body["applied"] is None


def test_put_policy_roundtrip(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-put", hostname="mo-put", app="Zabbix Server", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    payload = {
        "cpu_threshold": 80,
        "cpu_window_minutes": 2,
        "mem_threshold": 85,
        "mem_window_minutes": 3,
        "load_threshold": 2.0,
        "load_window_minutes": 10,
    }
    r = client.put("/api/v1/assets/mothers/mo-put/alert-policy", json=payload, headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["policy"] == payload
    assert body["applied"] is None  # 未绑定 Zabbix，仅存储
    assert body["apply_error"] == ""

    r2 = client.get("/api/v1/assets/mothers/mo-put/alert-policy", headers=_h(auth_token))
    assert r2.json()["policy"] == payload


def test_put_policy_validates_fields(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-bad", hostname="mo-bad", app="Zabbix Server", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.put(
        "/api/v1/assets/mothers/mo-bad/alert-policy",
        json={"cpu_threshold": 0, "cpu_window_minutes": 5, "mem_threshold": 90, "mem_window_minutes": 5, "load_threshold": 1.5, "load_window_minutes": 5},
        headers=_h(auth_token),
    )
    assert r.status_code == 422
    r2 = client.put(
        "/api/v1/assets/mothers/mo-bad/alert-policy",
        json={"cpu_threshold": 90, "cpu_window_minutes": 5, "mem_threshold": 90, "mem_window_minutes": 5, "load_threshold": 1.5, "load_window_minutes": 5},
        headers=_h(auth_token),
    )
    assert r2.status_code == 200


def test_policy_endpoints_404_for_non_mother(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="ch-1", hostname="ch-1", app="演示App", role="app", owner="", kind="child", tenant_id="tenant-default", extra={}))
    db.commit()
    assert client.get("/api/v1/assets/mothers/ch-1/alert-policy", headers=_h(auth_token)).status_code == 404
    r = client.put(
        "/api/v1/assets/mothers/ch-1/alert-policy",
        json={"cpu_threshold": 90, "cpu_window_minutes": 5, "mem_threshold": 90, "mem_window_minutes": 5, "load_threshold": 1.5, "load_window_minutes": 5},
        headers=_h(auth_token),
    )
    assert r.status_code == 404
