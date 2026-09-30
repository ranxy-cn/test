"""告警策略 v2：默认值 / 校验 / 存储 / 模板（秒制窗口 + 系统资源/进程端口 + 规则目录）。"""
from __future__ import annotations

import pytest

from app.services.alert_policy import (
    ALERT_LEVELS,
    POLICY_TEMPLATES,
    RULE_CATALOG,
    normalize_policy,
    rule_defaults,
)


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_normalize_policy_defaults_and_partial():
    p = normalize_policy(None)
    assert p["cpu_threshold"] == 90 and p["cpu_window_seconds"] == 300
    assert p["mem_level"] == "P1" and p["load_level"] == "P1"
    assert p["oom_enabled"] is False and p["oom_level"] == "P1"
    assert p["swap_enabled"] is False and p["swap_threshold"] == 80 and p["swap_level"] == "P2"
    assert p["bandwidth_window_seconds"] == 600
    assert p["process_items"] == ["nginx", "mysqld", "java"]
    assert p["port_items"] == [22, 80, 443]
    assert p["metrics_urls"] == []
    # 规则目录全量预置（可配置但默认关闭）
    assert set(p["rules"].keys()) == {r["id"] for r in RULE_CATALOG}
    assert all(c["enabled"] is False for c in p["rules"].values())

    # v1 分钟窗口兼容：×60 转秒
    p2 = normalize_policy({"cpu_threshold": 80, "cpu_window_minutes": 1})
    assert p2["cpu_threshold"] == 80 and p2["cpu_window_seconds"] == 60
    assert p2["mem_threshold"] == 90
    p3 = normalize_policy({"load_threshold": "2.5"})
    assert p3["load_threshold"] == 2.5
    # 规则覆盖：调整阈值/窗口/级别/开关
    p4 = normalize_policy({"rules": {"app_health": {"enabled": True, "threshold": 5, "level": "P1"}}})
    assert p4["rules"]["app_health"] == {"enabled": True, "threshold": 5.0, "window_seconds": 60, "level": "P1"}


def test_normalize_policy_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_policy({"cpu_threshold": 0})
    with pytest.raises(ValueError):
        normalize_policy({"mem_threshold": 100})
    with pytest.raises(ValueError):
        normalize_policy({"cpu_window_seconds": 5})  # 低于 10s 下限
    with pytest.raises(ValueError):
        normalize_policy({"load_threshold": "abc"})
    with pytest.raises(ValueError):
        normalize_policy({"swap_level": "P9"})
    with pytest.raises(ValueError):
        normalize_policy({"port_items": [0]})
    # metrics_urls 非法协议静默过滤（宽容处理）
    p5 = normalize_policy({"metrics_urls": ["ftp://x", "http://ok:9090/metrics"]})
    assert p5["metrics_urls"] == ["http://ok:9090/metrics"]


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
    # v1 分钟字段自动转秒存储
    g = client.get("/api/v1/assets/mothers/mother-10-1-0-8/alert-policy", headers=_h(auth_token))
    assert g.status_code == 200
    assert g.json()["policy"]["cpu_threshold"] == 85
    assert g.json()["policy"]["cpu_window_seconds"] == 120
    assert g.json()["policy"]["mem_threshold"] == 90

    r2 = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-bad", "ip": "10.1.0.9", "alert_policy": {"cpu_threshold": 0}},
        headers=_h(auth_token),
    )
    assert r2.status_code == 400


def test_get_policy_defaults_and_meta(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-pol", hostname="mo-pol", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.get("/api/v1/assets/mothers/mo-pol/alert-policy", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert body["policy"] == normalize_policy(None)
    assert body["defaults"] == normalize_policy(None)
    # 元数据：级别（含颜色）/ 规则目录 / 模板
    assert [lv["value"] for lv in body["levels"]] == list(ALERT_LEVELS)
    assert all(lv["color"] for lv in body["levels"])
    assert {c["id"] for c in body["catalog"]} == {r["id"] for r in RULE_CATALOG}
    assert {t["id"] for t in body["templates"]} == set(POLICY_TEMPLATES.keys())


def test_policy_meta_endpoint(auth_token, client):
    r = client.get("/api/v1/alert-policy/meta", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert len(body["catalog"]) >= 32  # 应用层 18 + 数据库层 17
    assert {t["id"] for t in body["templates"]} == {"standard", "strict", "relaxed"}
    strict = next(t for t in body["templates"] if t["id"] == "strict")
    assert strict["policy"]["oom_enabled"] is True
    assert strict["policy"]["rules"]["app_health"]["enabled"] is True


def test_put_policy_roundtrip(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-put", hostname="mo-put", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    payload = {
        "cpu_threshold": 80,
        "cpu_window_seconds": 120,
        "mem_threshold": 85,
        "mem_window_seconds": 180,
        "load_threshold": 2.0,
        "load_window_seconds": 600,
        "oom_enabled": True,
        "oom_level": "P1",
        "swap_enabled": True,
        "swap_threshold": 70,
        "swap_window_seconds": 300,
        "swap_level": "P2",
        "process_enabled": True,
        "process_items": ["nginx", "java"],
        "rules": {"app_health": {"enabled": True, "threshold": 3, "window_seconds": 60, "level": "P0"}},
    }
    r = client.put("/api/v1/assets/mothers/mo-put/alert-policy", json=payload, headers=_h(auth_token))
    assert r.status_code == 200
    got = r.json()["policy"]
    for k, v in payload.items():
        if k == "rules":
            assert got["rules"]["app_health"] == v["app_health"]
        else:
            assert got[k] == v

    r2 = client.get("/api/v1/assets/mothers/mo-put/alert-policy", headers=_h(auth_token))
    assert r2.json()["policy"]["swap_threshold"] == 70
    assert r2.json()["policy"]["rules"]["app_health"]["enabled"] is True


def test_put_policy_validates_fields(auth_token, client, db):
    from app.models import Asset

    db.add(Asset(id="mo-bad", hostname="mo-bad", app="运维平台", role="app", owner="", kind="mother", tenant_id="tenant-default", extra={}))
    db.commit()
    r = client.put(
        "/api/v1/assets/mothers/mo-bad/alert-policy",
        json={"cpu_threshold": 0},
        headers=_h(auth_token),
    )
    assert r.status_code == 400
    r2 = client.put(
        "/api/v1/assets/mothers/mo-bad/alert-policy",
        json={"cpu_threshold": 90, "cpu_window_seconds": 300},
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
        json={"cpu_threshold": 90},
        headers=_h(auth_token),
    )
    assert r.status_code == 404


# ===== 子机级策略（GET/PUT/DELETE /api/v1/assets/{id}/alert-policy）=====


def _full_policy(**over) -> dict:
    base = {
        "cpu_threshold": 90,
        "cpu_window_seconds": 300,
        "mem_threshold": 90,
        "mem_window_seconds": 300,
        "load_threshold": 1.5,
        "load_window_seconds": 300,
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

    # 子机覆盖为自有策略（如 92 号机：cpu 50%/60 秒）
    r = client.put(
        "/api/v1/assets/ch-ovr/alert-policy",
        json=_full_policy(cpu_threshold=50, cpu_window_seconds=60),
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
    assert r.status_code == 400


def test_asset_policy_404_for_unknown(auth_token, client):
    assert client.get("/api/v1/assets/no-such-asset/alert-policy", headers=_h(auth_token)).status_code == 404
    assert client.delete("/api/v1/assets/no-such-asset/alert-policy", headers=_h(auth_token)).status_code == 404


def test_rule_defaults_cover_catalog():
    rd = rule_defaults()
    assert len(rd) == len(RULE_CATALOG)
    levels = {c["level"] for c in rd.values()}
    assert levels <= set(ALERT_LEVELS)
