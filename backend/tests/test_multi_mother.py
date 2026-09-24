"""多母机管控一期：母机列表 / 登记母机 / 按母机总览 / 子机归属 / Zabbix 连通校验。"""
from __future__ import annotations

from sqlalchemy import or_

from app.models import Asset


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


def test_create_mother_requires_risk_ack(auth_token, client, db):
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-zbx-02", "ip": "10.0.0.9", "ack_risk": False},
        headers=_h(auth_token),
    )
    assert r.status_code == 400


def test_create_mother_bundled_and_overview(auth_token, client, db):
    # 默认母机模拟线上已部署状态（zabbix.url 已写回）→ 显示"母机自身"合成子机
    db.add(
        _mk(
            "devops-mother-186",
            kind="mother",
            ip="124.221.251.186",
            extra={"zabbix": {"url": "http://124.221.251.186:8081"}, "provision": {"ip": "124.221.251.186", "port": 22}},
        )
    )
    db.add(_mk("node-legacy"))  # 存量未归属子机 → 默认母机
    db.commit()

    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-zbx-02", "ip": "10.0.0.9", "db_mode": "bundled", "ack_risk": True},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["id"] == "mother-10-0-0-9"
    assert r.json()["kind"] == "mother"
    assert r.json()["db_mode"] == "bundled"

    # 列表：两台母机，默认母机带存量子机计数
    r2 = client.get("/api/v1/assets/mothers", headers=_h(auth_token))
    assert r2.status_code == 200
    items = {i["id"]: i for i in r2.json()["items"]}
    assert set(items) == {"devops-mother-186", "mother-10-0-0-9"}
    assert items["devops-mother-186"]["is_default"] is True
    # 默认母机已绑定 zabbix.url → 计入"母机自身"合成子机（真实子机 + 1）
    real_children = (
        db.query(Asset)
        .filter(Asset.kind != "mother", Asset.id != "devops-mother-186")
        .filter(or_(Asset.mother_id == "devops-mother-186", Asset.mother_id == ""))
        .count()
    )
    assert items["devops-mother-186"]["children_count"] == real_children + 1
    # 未部署的新母机：没有"母机自身"合成子机，children_count 不 +1（否则母机永远删不掉）
    assert items["mother-10-0-0-9"]["children_count"] == 0

    # 未部署的母机不显示"母机自身"行（Zabbix 里没有该主机，显示出来就是一台删不掉的假子机）
    r3 = client.get("/api/v1/assets/mothers/mother-10-0-0-9/overview", headers=_h(auth_token))
    assert r3.status_code == 200
    assert r3.json()["children"] == []

    # 部署成功（写回 zabbix.url）后："母机自身"行出现，children_count +1
    row = db.get(Asset, "mother-10-0-0-9")
    row.extra = {
        **(row.extra or {}),
        "deploy": {"status": "success", "version": "6.4"},
        "zabbix": {**((row.extra or {}).get("zabbix") or {}), "url": "http://10.0.0.9:8081"},
    }
    db.commit()
    r3b = client.get("/api/v1/assets/mothers/mother-10-0-0-9/overview", headers=_h(auth_token))
    assert [c["id"] for c in r3b.json()["children"]] == ["mother-10-0-0-9:self"]
    # 母机自身行 IP 显示登记的母机地址（不再被 Zabbix 自监控接口的 127.0.0.1 覆盖）
    assert r3b.json()["children"][0]["ip"] == "10.0.0.9"
    r2b = client.get("/api/v1/assets/mothers", headers=_h(auth_token))
    items2 = {i["id"]: i for i in r2b.json()["items"]}
    assert items2["mother-10-0-0-9"]["children_count"] == 1

    r4 = client.get("/api/v1/assets/mothers/devops-mother-186/overview", headers=_h(auth_token))
    assert r4.status_code == 200
    ids = {c["id"] for c in r4.json()["children"]}
    assert "node-legacy" in ids and "devops-mother-186:self" in ids and "mother-10-0-0-9" not in ids
    # 已部署母机自身行的 IP = 登记地址（而非 Zabbix 接口的 127.0.0.1）
    self_row = next(c for c in r4.json()["children"] if c["id"] == "devops-mother-186:self")
    assert self_row["ip"] == "124.221.251.186"


def test_create_mother_external_db_and_duplicate(auth_token, client, db):
    body = {
        "hostname": "ops-zbx-03",
        "ip": "10.0.0.10",
        "db_mode": "external",
        "external_db": {"host": "127.0.0.1", "port": 3306, "user": "zabbix", "password": "pw", "database": "zabbix"},
        "zabbix_url": "http://10.0.0.10:8081",
        "zabbix_user": "Admin",
        "zabbix_password": "zabbix",
        "ack_risk": True,
    }
    r = client.post("/api/v1/assets/mothers", json=body, headers=_h(auth_token))
    assert r.status_code == 200
    assert r.json()["db_mode"] == "external"

    # 外部库信息入 extra.zabbix.db；详情接口密码脱敏
    r2 = client.get("/api/v1/assets/mother-10-0-0-10", headers=_h(auth_token))
    assert r2.status_code == 200
    z = r2.json()["extra"]["zabbix"]
    assert z["db"]["host"] == "127.0.0.1" and z["password"] == "******" and z["db"]["password"] == "******"

    # 同 IP 重复登记 → 409；外部库缺字段 → 400
    r3 = client.post("/api/v1/assets/mothers", json=body, headers=_h(auth_token))
    assert r3.status_code == 409
    bad = {**body, "hostname": "ops-zbx-04", "ip": "10.0.0.11", "external_db": {"host": "127.0.0.1"}}
    r4 = client.post("/api/v1/assets/mothers", json=bad, headers=_h(auth_token))
    assert r4.status_code == 400


def test_provision_attach_to_mother(client, db, monkeypatch, auth_token):
    from app.services import provision as prov_mod

    captured = {}

    def fake_provision(asset_id, **kw):
        captured["asset_id"] = asset_id
        captured["zabbix_server"] = kw.get("zabbix_server")

    monkeypatch.setattr(prov_mod, "provision_node", fake_provision)

    db.add(_mk("mother-10-0-0-9", kind="mother", ip="10.0.0.9"))
    db.commit()

    # 新增节点显式挂新母机：未填 zabbix_server 时默认指向母机 IP
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.0.0.55", "password": "x", "zabbix_server": "", "mother_id": "mother-10-0-0-9"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["zabbix_server"] == "10.0.0.9"
    import time

    for _ in range(30):
        if captured:
            break
        time.sleep(0.05)
    assert captured.get("zabbix_server") == "10.0.0.9"
    a = db.get(Asset, captured["asset_id"])
    assert a.mother_id == "mother-10-0-0-9"

    # 挂不存在的母机 → 404
    r2 = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.0.0.56", "password": "x", "zabbix_server": "1.2.3.4", "mother_id": "no-such"},
        headers=_h(auth_token),
    )
    assert r2.status_code == 404


def test_verify_zabbix_endpoint(auth_token, client):
    # 不可达地址 → ok=False 且不抛 500
    r = client.post(
        "/api/v1/assets/verify-zabbix",
        json={"url": "http://127.0.0.1:1", "user": "Admin", "password": "zabbix"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_uninstall_mother_rejects_undeployed(auth_token, client, db):
    """未部署成功的母机（无 zabbix.url）：SSH 卸载直接 400，引导走「仅删除记录」。"""
    db.add(_mk("mother-10-0-0-9", kind="mother", ip="10.0.0.9"))  # extra.zabbix 无 url
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers/mother-10-0-0-9/uninstall",
        json={"ip": "10.0.0.9", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 400
    assert "仅删除记录" in r.json()["detail"] or "没有 Zabbix 栈" in r.json()["detail"]
    # 记录保留
    db.expire_all()
    assert db.get(Asset, "mother-10-0-0-9") is not None


def test_create_mother_port_rules(auth_token, client, db):
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.commit()
    base = {"hostname": "ops-zbx-p", "ip": "10.9.9.9", "ack_risk": True}

    # Web 端口与 Trapper 端口相同 → 400
    r = client.post("/api/v1/assets/mothers", json={**base, "zabbix_web_port": 8081, "zabbix_trapper_port": 8081}, headers=_h(auth_token))
    assert r.status_code == 400
    # 平台保留端口 → 400
    r2 = client.post("/api/v1/assets/mothers", json={**base, "zabbix_web_port": 8080}, headers=_h(auth_token))
    assert r2.status_code == 400
    r3 = client.post("/api/v1/assets/mothers", json={**base, "zabbix_trapper_port": 3306}, headers=_h(auth_token))
    assert r3.status_code == 400
    # 超出合法范围 → 422
    r4 = client.post("/api/v1/assets/mothers", json={**base, "zabbix_web_port": 80}, headers=_h(auth_token))
    assert r4.status_code == 422


def test_create_mother_custom_ports_persist_and_listed(auth_token, client, db):
    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-zbx-77", "ip": "10.9.9.9", "zabbix_web_port": 18081, "zabbix_trapper_port": 20051, "ack_risk": True},
        headers=_h(auth_token),
    )
    assert r.status_code == 200

    # extra.zabbix 落库（详情脱敏接口可见）
    r2 = client.get("/api/v1/assets/mother-10-9-9-9", headers=_h(auth_token))
    assert r2.status_code == 200
    z = r2.json()["extra"]["zabbix"]
    assert z["web_port"] == 18081 and z["trapper_port"] == 20051

    # 母机列表输出端口
    r3 = client.get("/api/v1/assets/mothers", headers=_h(auth_token))
    items = {i["id"]: i for i in r3.json()["items"]}
    assert items["mother-10-9-9-9"]["zabbix_web_port"] == 18081
    assert items["mother-10-9-9-9"]["zabbix_trapper_port"] == 20051
    # 未自定义端口的母机回落默认值
    assert items["devops-mother-186"]["zabbix_web_port"] == 8081
    assert items["devops-mother-186"]["zabbix_trapper_port"] == 10051


def test_provision_uses_mother_trapper_port(auth_token, client, db, monkeypatch):
    from app.services import provision as prov_mod

    captured = {}

    def fake_provision(asset_id, **kw):
        captured["zabbix_server"] = kw.get("zabbix_server")

    monkeypatch.setattr(prov_mod, "provision_node", fake_provision)

    db.add(_mk("devops-mother-186", kind="mother", ip="124.221.251.186"))
    db.commit()
    client.post(
        "/api/v1/assets/mothers",
        json={"hostname": "ops-zbx-78", "ip": "10.9.9.10", "zabbix_trapper_port": 20051, "ack_risk": True},
        headers=_h(auth_token),
    )

    # 非默认 Trapper 端口 → zabbix_server 显式带 ip:port
    r = client.post(
        "/api/v1/assets/provision",
        json={"ip": "10.0.0.77", "password": "x", "zabbix_server": "", "mother_id": "mother-10-9-9-10"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["zabbix_server"] == "10.9.9.10:20051"
    import time

    for _ in range(30):
        if captured:
            break
        time.sleep(0.05)
    assert captured.get("zabbix_server") == "10.9.9.10:20051"


class _FakeZbx:
    """按 method 返回预制数据，模拟 Zabbix 客户端的 _rpc。"""

    def __init__(self, hosts, items, boom=False):
        self.hosts, self.items, self.boom = hosts, items, boom

    def _rpc(self, method, params):
        if self.boom:
            raise RuntimeError("zabbix down")
        if method == "host.get":
            return self.hosts
        frag = params["search"]["key_"]
        return [it for it in self.items if frag in it["key_"] and it["hostid"] in params["hostids"]]


def test_children_metrics_summary_mapping():
    from app.api import _children_metrics_summary

    zbx = _FakeZbx(
        hosts=[{"hostid": "10084", "host": "node-1", "name": "node-1 显示名"}],
        items=[
            {"itemid": "1", "hostid": "10084", "key_": "system.cpu.util", "lastvalue": "23.456"},
            # pavailable 是"可用内存%"，与监控详情口径一致需转成"已用%"：100 - 61.2 = 38.8
            {"itemid": "2", "hostid": "10084", "key_": "vm.memory.size[pavailable]", "lastvalue": "61.2"},
            {"itemid": "3", "hostid": "10084", "key_": "vfs.fs.size[/,pused]", "lastvalue": "45.1"},
            {"itemid": "4", "hostid": "10084", "key_": "system.cpu.load[all,avg1]", "lastvalue": "0.42"},
        ],
    )
    rows = [{"id": "node-1", "zabbix_host": "node-1", "hostname": "node-1"}]
    out = _children_metrics_summary(zbx, rows)
    assert out["node-1"] == {"cpu": 23.46, "mem": 38.8, "disk": 45.1, "load": 0.42}

    # 主机匹配不上 → 空 dict
    rows2 = [{"id": "node-x", "zabbix_host": "nope", "hostname": "nope"}]
    assert _children_metrics_summary(zbx, rows2) == {}


def test_children_metrics_summary_degrades_silently():
    from app.api import _children_metrics_summary

    zbx = _FakeZbx(hosts=[], items=[], boom=True)
    assert _children_metrics_summary(zbx, [{"id": "n", "zabbix_host": "n", "hostname": "n"}]) == {}
