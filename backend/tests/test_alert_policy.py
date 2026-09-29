"""母机告警策略：默认值 / 校验 / 存储（纯平台存储，供本地越限判定与展示）。"""
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
            "hostname": "ops-pol",
            "ip": "10.1.0.8",
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
        json={"hostname": "ops-bad", "ip": "10.1.0.9", "alert_policy": {"cpu_threshold": 0}},
        headers=_h(auth_token),
    )
    assert r2.status_code == 400


def test_get_policy_defaults_for_mother_without_policy(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-pol", hostname="mo-pol", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.get("/api/v1/assets/mothers/mo-pol/alert-policy", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["policy"] == DEFAULT_POLICY
    assert body["defaults"] == DEFAULT_POLICY


def test_put_policy_roundtrip(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-put", hostname="mo-put", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
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
    assert r.json()["policy"] == payload

    r2 = client.get("/api/v1/assets/mothers/mo-put/alert-policy", headers=_h(auth_token))
    assert r2.json()["policy"] == payload


def test_put_policy_validates_fields(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-bad", hostname="mo-bad", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
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


# ===== 子机级策略（GET/PUT/DELETE /api/v1/assets/{id}/alert-policy）=====


def _full_policy(**over) -> dict:
    base = {
        "cpu_threshold": 90,
        "cpu_window_minutes": 5,
        "mem_threshold": 90,
        "mem_window_minutes": 5,
        "load_threshold": 1.5,
        "load_window_minutes": 5,
    }
    base.update(over)
    return base


def test_child_policy_inherits_mother(auth_token, client, db):
    from app.models import Asset

    db.add(
        Asset(
            id="mo-inh",
            hostname="mo-inh",
            app="运维平台",
            role="app",
            owner="",
            kind="mother",
            tenant_id="tenant-default",
            extra={"alert_policy": _full_policy(cpu_threshold=70)},
        )
    )
    db.add(
        Asset(
            id="ch-inh",
            hostname="ch-inh",
            app="演示App",
            role="app",
            owner="",
            kind="child",
            mother_id="mo-inh",
            tenant_id="tenant-default",
            extra={},
        )
    )
    db.commit()

    r = client.get("/api/v1/assets/ch-inh/alert-policy", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["inherited"] is True
    assert body["source"] == "mother"
    assert body["policy"]["cpu_threshold"] == 70

    # 母机策略变更 → 子机生效值跟随
    p = client.put("/api/v1/assets/mo-inh/alert-policy", json=_full_policy(cpu_threshold=60), headers=_h(auth_token))
    assert p.status_code == 200
    g = client.get("/api/v1/assets/ch-inh/alert-policy", headers=_h(auth_token))
    assert g.json()["policy"]["cpu_threshold"] == 60


def test_child_policy_override_and_reset(auth_token, client, db):
    from app.models import Asset

    db.add(
        Asset(
            id="mo-ovr",
            hostname="mo-ovr",
            app="运维平台",
            role="app",
            owner="",
            kind="mother",
            tenant_id="tenant-default",
            extra={"alert_policy": _full_policy(cpu_threshold=70)},
        )
    )
    db.add(
        Asset(
            id="ch-ovr",
            hostname="ch-ovr",
            app="演示App",
            role="app",
            owner="",
            kind="child",
            mother_id="mo-ovr",
            tenant_id="tenant-default",
            extra={},
        )
    )
    db.commit()

    # 子机覆盖为自有策略（如 92 号机：cpu 50%/1 分钟）
    r = client.put(
        "/api/v1/assets/ch-ovr/alert-policy",
        json=_full_policy(cpu_threshold=50, cpu_window_minutes=1),
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["inherited"] is False
    g = client.get("/api/v1/assets/ch-ovr/alert-policy", headers=_h(auth_token))
    assert g.json()["inherited"] is False
    assert g.json()["source"] == "self"
    assert g.json()["policy"]["cpu_threshold"] == 50

    # 母机改策略不再影响子机自有策略
    client.put("/api/v1/assets/mo-ovr/alert-policy", json=_full_policy(cpu_threshold=99), headers=_h(auth_token))
    g2 = client.get("/api/v1/assets/ch-ovr/alert-policy", headers=_h(auth_token))
    assert g2.json()["policy"]["cpu_threshold"] == 50

    # 恢复继承母机
    d = client.delete("/api/v1/assets/ch-ovr/alert-policy", headers=_h(auth_token))
    assert d.status_code == 200
    g3 = client.get("/api/v1/assets/ch-ovr/alert-policy", headers=_h(auth_token))
    assert g3.json()["inherited"] is True
    assert g3.json()["policy"]["cpu_threshold"] == 99


def test_child_policy_validates_fields(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="ch-bad", hostname="ch-bad", app="演示App", role="app", owner="", kind="child", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.put(
        "/api/v1/assets/ch-bad/alert-policy",
        json=_full_policy(cpu_threshold=0),
        headers=_h(auth_token),
    )
    assert r.status_code == 422


def test_asset_policy_404_for_unknown(auth_token, client):
    assert client.get("/api/v1/assets/no-such-asset/alert-policy", headers=_h(auth_token)).status_code == 404
    assert client.delete("/api/v1/assets/no-such-asset/alert-policy", headers=_h(auth_token)).status_code == 404
