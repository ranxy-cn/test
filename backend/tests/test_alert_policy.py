"""母机告警策略：默认值 / 校验 / 存储 / 绑定 Zabbix 时的模板同步（mock 下跳过同步）。"""
from __future__ import annotations

import re

import pytest

from app.services.alert_policy import DEFAULT_POLICY, normalize_policy


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _FakeZc:
    """最小 Zabbix 假客户端：主动/被动两棵模板树，各带一个 CPU 触发器子模块。"""

    def __init__(self):
        self.templates = {
            "10001": {"templateid": "10001", "host": "Template OS Linux by Zabbix agent"},
            "10284": {"templateid": "10284", "host": "Template OS Linux by Zabbix agent active"},
            "10101": {"templateid": "10101", "host": "Tpl-CPU-passive"},
            "10301": {"templateid": "10301", "host": "Tpl-CPU-active"},
        }
        self.parent_map = {"10001": ["10101"], "10284": ["10301"]}
        self.triggers = {
            "10101": [{"triggerid": "T1", "expression": "avg({1})>90",
                       "functions": [{"functionid": "1", "itemid": "I1", "function": "min", "parameter": "5m"}]}],
            "10301": [{"triggerid": "T2", "expression": "avg({2})>90",
                       "functions": [{"functionid": "2", "itemid": "I2", "function": "min", "parameter": "5m"}]}],
        }
        self.items = {"I1": "system.cpu.util", "I2": "system.cpu.util"}
        self.macros: list[dict] = []
        self.calls: list[tuple] = []
        self._next = 10

    def _rpc(self, method, params):
        self.calls.append((method, params))
        if method == "template.get":
            if params.get("filter", {}).get("host"):
                host = params["filter"]["host"]
                return [t for t in self.templates.values() if t["host"] == host]
            tid = str(params["templateids"][0])
            if "selectParentTemplates" in params:
                return [{"templateid": tid, "parentTemplates": [{"templateid": p} for p in self.parent_map.get(tid, [])]}]
            return [dict(self.templates[tid])]
        if method == "trigger.get":
            if params.get("triggerids"):
                tid = str(params["triggerids"][0])
                for lst in self.triggers.values():
                    for t in lst:
                        if t["triggerid"] == tid:
                            return [dict(t, description="d", priority="2", comments="", url="", status="0", type="0", manual_close="0")]
                return []
            return list(self.triggers.get(str(params["templateids"][0]), []))
        if method == "item.get":
            return [{"itemid": k, "key_": v} for k, v in self.items.items()]
        if method == "usermacro.get":
            want = params.get("filter", {}).get("macro", [])
            want = {want} if isinstance(want, str) else set(want)
            rows = [dict(m) for m in self.macros]
            return [m for m in rows if m["macro"] in want] if want else rows
        if method == "usermacro.create":
            self.macros.append({
                "hostmacroid": str(self._next), "hostid": str(params["hostid"]),
                "macro": params["macro"], "value": str(params["value"]),
            })
            self._next += 1
            return {"hostmacroids": [str(self._next - 1)]}
        if method == "usermacro.update":
            for m in self.macros:
                if m["hostmacroid"] == params["hostmacroid"]:
                    m["value"] = str(params["value"])
            return True
        if method == "trigger.update":
            # 同步表达式里的 min 参数，模拟 Zabbix 传播回模板
            m = re.search(r"min\((\d+m)\)", params["expression"])
            for lst in self.triggers.values():
                for t in lst:
                    if t["triggerid"] == params["triggerid"]:
                        for f in t["functions"]:
                            if f["function"] == "min" and m:
                                f["parameter"] = m.group(1)
            return True
        raise AssertionError(method)


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


def test_apply_policy_covers_active_and_passive_templates():
    """策略必须同时落到主动/被动两棵树：宏各挂一份、窗口全部改写。"""
    from app.services.alert_policy import apply_policy

    zc = _FakeZc()
    policy = normalize_policy({"cpu_window_minutes": 2, "mem_window_minutes": 2, "load_window_minutes": 2})
    res = apply_policy(zc, policy)
    cpu_macros = [m for m in zc.macros if m["macro"] == "{$CPU.UTIL.CRIT}"]
    assert {m["hostid"] for m in cpu_macros} == {"10001", "10284"}
    assert all(m["value"] == "90" for m in cpu_macros)
    updates = [p for p in zc.calls if p[0] == "trigger.update"]
    assert len(updates) == 2  # 两棵树各一个 High CPU 触发器
    assert all("min(2m)" in str(p[1].get("expression")) for p in updates)
    assert res["changes"]


def test_apply_policy_rewrites_window_and_reread():
    from app.services.alert_policy import apply_policy, read_applied_policy

    zc = _FakeZc()
    apply_policy(zc, normalize_policy({"cpu_threshold": 80, "cpu_window_minutes": 1}))
    applied = read_applied_policy(zc)
    assert applied["cpu_threshold"] == 80
    assert applied["cpu_window_minutes"] == 1
    # 二次应用应无变更（幂等）
    zc2 = _FakeZc()
    apply_policy(zc2, normalize_policy(None))
    again = apply_policy(zc2, normalize_policy(None))
    assert again["changes"] == []
