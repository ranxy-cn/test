"""多母机管控：母机列表 / 纯业务登记母机 / 按母机总览 / 子机归属 / 级联删除。

母机为纯业务归属节点：子机 agent 按 mother_id 归属上报，平台不在母机上部署任何监控栈。
"""
from __future__ import annotations

from app.models import Asset, AuditLog, Ticket
from app.services import alert_policy


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(aid: str, kind: str = "child", mother_id: str = "", ip: str = "127.0.0.1", extra: dict | None = None) -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        kind=kind,
        mother_id=mother_id,
        tenant_id="tenant-default",
        extra=extra if extra is not None else {"provision": {"ip": ip, "port": 1, "status": "registered"}},
    )


def test_default_mother_is_kind_mother(auth_token, client, db):
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.commit()
    row = db.get(Asset, "devops-mother-186")
    assert row is not None and row.kind == "mother"


def test_create_mother_pure_registration(auth_token, client, db):
    """登记母机（API 兼容路径，无密码不验证）：id 规则 mother-<ip>、审计留痕、台账初始不可达。"""
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-02", "ip": "10.0.0.9", "owner": "ops"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "mother-10-0-0-9"
    assert body["kind"] == "mother"
    assert body["reachable"] is False  # 未做 SSH 验证，不假显示在线
    assert body["self_child_id"] == ""

    row = db.get(Asset, "mother-10-0-0-9")
    assert row.extra["provision"] == {"ip": "10.0.0.9", "port": 22, "status": "registered"}
    assert row.extra["alert_policy"] == alert_policy.normalize_policy(None)  # 默认策略
    assert db.query(AuditLog).filter(AuditLog.event_type == "mother_create").count() == 1


def test_create_mother_duplicate_and_hostname_conflict(auth_token, client, db):
    """同 IP 重复登记 → 409；hostname 与已有资产撞名 → 409。"""
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-02", "ip": "10.0.0.9"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200

    r2 = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-03", "ip": "10.0.0.9"},
        headers=_h(auth_token),
    )
    assert r2.status_code == 409
    assert "已登记为母机" in r2.json()["detail"]

    r3 = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-m-02", "ip": "10.0.1.9"},
        headers=_h(auth_token),
    )
    assert r3.status_code == 409
    assert "主机名已被资产占用" in r3.json()["detail"]


def test_create_mother_alert_policy(auth_token, client, db):
    """告警策略：合法值入库（缺省补默认）；非法值 400。"""
    policy = {"cpu_threshold": 90, "cpu_window_minutes": 5}
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "m-pol", "ip": "10.1.1.1", "alert_policy": policy},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    row = db.get(Asset, "mother-10-1-1-1")
    assert row.extra["alert_policy"]["cpu_threshold"] == 90
    assert row.extra["alert_policy"]["cpu_window_minutes"] == 5
    assert row.extra["alert_policy"]["mem_threshold"] == alert_policy.DEFAULT_POLICY["mem_threshold"]

    r2 = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "m-pol2", "ip": "10.1.1.2", "alert_policy": {"cpu_threshold": 200}},
        headers=_h(auth_token),
    )
    assert r2.status_code == 400
    assert "1~99" in r2.json()["detail"]


def test_mothers_list_children_count(auth_token, client, db):
    """默认母机计存量未归属子机；非默认母机只计自己名下。"""
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.add(_mk("node-legacy"))  # mother_id="" → 归默认母机
    db.add(_mk("node-a", mother_id="devops-mother-186"))
    db.commit()
    client.post("/api/v1/assets/mothers", json={"hostname": "ops-m-09", "ip": "10.0.0.9"}, headers=_h(auth_token))
    db.add(_mk("node-b", mother_id="mother-10-0-0-9"))
    db.commit()

    r = client.get("/api/v1/assets/mothers", headers=_h(auth_token))
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    assert set(items) == {"devops-mother-186", "mother-10-0-0-9"}
    assert items["devops-mother-186"]["is_default"] is True
    # 默认母机计数 = 名下 + 存量未归属（含 seed 演示资产），动态计算避免与 seed 耦合
    expected_default = (
        db.query(Asset)
        .filter(Asset.kind != "mother")
        .filter((Asset.mother_id == "devops-mother-186") | (Asset.mother_id == ""))
        .count()
    )
    assert items["devops-mother-186"]["children_count"] == expected_default
    assert expected_default >= 2  # 至少含 node-legacy + node-a
    assert items["mother-10-0-0-9"]["children_count"] == 1  # node-b
    assert r.json()["default_id"] == "devops-mother-186"


def test_mother_overview_children_and_agent_metrics(auth_token, client, db, monkeypatch):
    """总览：子机按归属过滤；实时指标来自 agent 上报缓存；未上报 metrics=None。"""
    import app.routers.agent_api as agent_api

    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.add(_mk("node-legacy"))
    db.commit()
    monkeypatch.setattr(
        agent_api, "LATEST", {"node-legacy": {"cpu": 23.46, "mem": 38.8, "disk": 45.1, "load1": 0.42}}
    )

    r = client.get("/api/v1/assets/mothers/devops-mother-186/overview", headers=_h(auth_token))
    assert r.status_code == 200
    children = {c["id"]: c for c in r.json()["children"]}
    assert "node-legacy" in children  # 存量未归属子机归默认母机（另含 seed 演示资产）
    assert children["node-legacy"]["metrics"] == {
        "cpu": 23.46,
        "mem": 38.8,
        "disk": 45.1,
        "load": 0.42,
        "net_rx_bps": None,
        "net_tx_bps": None,
    }
    assert all(not i.startswith("mother-") for i in children)  # 母机自身不在子机列表

    # 未上报的子机 metrics=None（前端显示 "-"）；其他母机的子机不出现
    client.post("/api/v1/assets/mothers", json={"hostname": "ops-m-10", "ip": "10.0.0.10"}, headers=_h(auth_token))
    db.add(_mk("node-quiet"))
    db.add(_mk("node-x", mother_id="mother-10-0-0-10"))
    db.commit()
    r2 = client.get("/api/v1/assets/mothers/devops-mother-186/overview", headers=_h(auth_token))
    by_id = {c["id"]: c for c in r2.json()["children"]}
    assert by_id["node-quiet"]["metrics"] is None
    assert "node-x" not in by_id

    r3 = client.get("/api/v1/assets/mothers/mother-10-0-0-10/overview", headers=_h(auth_token))
    ids3 = {c["id"] for c in r3.json()["children"]}
    assert ids3 == {"node-x"}

    # 不存在的母机 → 404
    r4 = client.get("/api/v1/assets/mothers/no-such/overview", headers=_h(auth_token))
    assert r4.status_code == 404


def test_uninstall_mother_cascades_children(auth_token, client, db):
    """删除母机：纯台账级联（名下子机一并删），其他母机的子机保留。"""
    db.add(_mk("mother-10-0-0-9", kind="mother", ip="10.0.0.9"))
    db.add(_mk("node-c1", mother_id="mother-10-0-0-9"))
    db.add(_mk("node-c2", mother_id="mother-10-0-0-9"))
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.add(_mk("node-other", mother_id="devops-mother-186"))
    db.commit()

    r = client.post("/api/v1/assets/mothers/mother-10-0-0-9/uninstall", headers=_h(auth_token))
    assert r.status_code == 200
    assert r.json()["deleted"] == "mother-10-0-0-9"
    assert sorted(r.json()["cascade_children"]) == ["node-c1", "node-c2"]

    db.expire_all()
    assert db.get(Asset, "mother-10-0-0-9") is None
    assert db.get(Asset, "node-c1") is None and db.get(Asset, "node-c2") is None
    assert db.get(Asset, "node-other") is not None  # 他母机子机不受影响

    audit = db.query(AuditLog).filter(AuditLog.event_type == "mother_uninstall").all()
    assert any(x.result["asset_id"] == "mother-10-0-0-9" for x in audit)


def test_uninstall_mother_404_and_ref_guard(auth_token, client, db):
    """不存在/非母机 → 404；母机或子机被工单引用 → 409 拒绝级联删除。"""
    r = client.post("/api/v1/assets/mothers/no-such/uninstall", headers=_h(auth_token))
    assert r.status_code == 404

    db.add(_mk("node-not-mother"))
    db.commit()
    r2 = client.post("/api/v1/assets/mothers/node-not-mother/uninstall", headers=_h(auth_token))
    assert r2.status_code == 404

    db.add(_mk("mother-10-0-0-9", kind="mother", ip="10.0.0.9"))
    db.add(_mk("node-c1", mother_id="mother-10-0-0-9"))
    db.commit()
    db.add(
        Ticket(
            number="T-1",
            idempotency_key="k-1",
            asset_id="node-c1",  # 子机被工单引用 → 母机级联删除必须拒绝
            tenant_id="t1",
            title="演示工单",
            event_id="e-1",
        )
    )
    db.commit()
    r3 = client.post("/api/v1/assets/mothers/mother-10-0-0-9/uninstall", headers=_h(auth_token))
    assert r3.status_code == 409
    assert "被引用" in r3.json()["detail"]
    db.expire_all()
    assert db.get(Asset, "mother-10-0-0-9") is not None  # 不产生部分删除


def test_children_agent_summary_mapping(monkeypatch):
    """_children_agent_summary：LATEST 命中 → 指标全量（含网络，缺省 None）；未命中 → 不在结果里。"""
    import app.routers.agent_api as agent_api
    from app.api import _children_agent_summary

    monkeypatch.setattr(
        agent_api,
        "LATEST",
        {
            "node-1": {"cpu": 23.46, "mem": 38.8, "disk": 45.1, "load1": 0.42},
            "node-load": {"cpu": 1.0, "mem": 2.0, "disk": 3.0, "load": 5.5},  # load 键而非 load1
        },
    )
    rows = [{"id": "node-1"}, {"id": "node-2"}]
    out = _children_agent_summary(rows)
    assert out == {
        "node-1": {
            "cpu": 23.46,
            "mem": 38.8,
            "disk": 45.1,
            "load": 0.42,
            "net_rx_bps": None,
            "net_tx_bps": None,
        }
    }

    out2 = _children_agent_summary([{"id": "node-load"}, {"id": "node-x"}])
    assert out2 == {
        "node-load": {"cpu": 1.0, "mem": 2.0, "disk": 3.0, "load": 5.5, "net_rx_bps": None, "net_tx_bps": None}
    }
