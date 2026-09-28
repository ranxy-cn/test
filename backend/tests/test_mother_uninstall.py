"""母机删除（级联删除子机 + 远程卸载 Zabbix 栈）与安装详情接口。"""
from __future__ import annotations

import time

from app.models import Asset, MaintenanceWindow, Ticket, utcnow


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_mother(aid: str, ip: str = "10.0.0.9") -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="Zabbix Server",
        role="mother",
        env="prod",
        owner="张三",
        kind="mother",
        db_mode="bundled",
        tenant_id="tenant-default",
        extra={
            "provision": {"ip": ip, "port": 1},
            "deploy": {"status": "success", "version": "5.0.47", "ip": ip, "port": 22, "username": "root"},
            "zabbix": {
                "url": f"http://{ip}:8081",
                "user": "Admin",
                "password": "zabbix",
                "web_port": 8081,
                "trapper_port": 10051,
                "db": {"mode": "bundled"},
            },
        },
    )


def _mk_child(aid: str, mother_id: str) -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        kind="node",
        mother_id=mother_id,
        tenant_id="tenant-default",
        extra={"provision": {"ip": "127.0.0.1", "port": 1}},
    )


def test_delete_mother_cascades_children(auth_token, client, db):
    """级联删除：母机删除时名下全部子机一并删除。"""
    db.add(_mk_mother("m-1"))
    db.add(_mk_child("node-1", "m-1"))
    db.add(_mk_child("node-1b", "m-1"))
    # 其他母机的子机不应被误删
    db.add(_mk_mother("m-other", ip="10.0.0.8"))
    db.add(_mk_child("node-other", "m-other"))
    db.commit()
    r = client.delete("/api/v1/assets/m-1", headers=_h(auth_token))
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["cascade_children"]) == ["node-1", "node-1b"]
    assert db.get(Asset, "m-1") is None
    assert db.get(Asset, "node-1") is None
    assert db.get(Asset, "node-1b") is None
    # 其他母机及其子机不受影响
    assert db.get(Asset, "m-other") is not None
    assert db.get(Asset, "node-other") is not None


def test_delete_mother_without_children_ok(auth_token, client, db):
    """边界回归：无子机的母机仅删记录仍正常。"""
    db.add(_mk_mother("m-1b"))
    db.commit()
    r = client.delete("/api/v1/assets/m-1b", headers=_h(auth_token))
    assert r.status_code == 200
    assert r.json()["cascade_children"] == []
    assert db.get(Asset, "m-1b") is None


def test_delete_mother_blocked_when_child_referenced(auth_token, client, db):
    """异常场景：任一子机被维护窗口引用 → 整体拒绝，不产生部分删除。"""
    db.add(_mk_mother("m-1c"))
    db.add(_mk_child("node-1c", "m-1c"))
    db.add(MaintenanceWindow(asset_id="node-1c", starts_at=utcnow(), ends_at=utcnow(), reason="r"))
    db.commit()
    r = client.delete("/api/v1/assets/m-1c", headers=_h(auth_token))
    assert r.status_code == 409
    assert "maintenance_windows" in r.json()["detail"]
    # 母机与子机记录均保留
    assert db.get(Asset, "m-1c") is not None
    assert db.get(Asset, "node-1c") is not None


def test_delete_mother_blocked_when_mother_referenced(auth_token, client, db):
    """异常场景：母机自身被工单引用 → 拒绝。"""
    db.add(_mk_mother("m-1d"))
    db.add(Ticket(
        number="T-1",
        idempotency_key="k-1",
        asset_id="m-1d",
        tenant_id="tenant-default",
        title="t",
        event_id="e-1",
    ))
    db.commit()
    r = client.delete("/api/v1/assets/m-1d", headers=_h(auth_token))
    assert r.status_code == 409
    assert "tickets" in r.json()["detail"]
    assert db.get(Asset, "m-1d") is not None


def test_uninstall_mother_with_children_cascades_on_success(auth_token, client, db, monkeypatch):
    """卸载并删除：名下有子机时允许启动，卸载成功后母机+子机一并删除。"""
    from app.services import mother_deploy as md

    monkeypatch.setattr(
        md, "uninstall_stack",
        lambda ip, port, username, password, logs: logs.append("远程卸载完成：容器已移除，安装目录（含监控数据）已删除"),
    )
    db.add(_mk_mother("m-2"))
    db.add(_mk_child("node-2", "m-2"))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers/m-2/uninstall",
        json={"ip": "10.0.0.9", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200

    # 后台线程完成卸载后，母机与子机的台账记录都被删除
    for _ in range(60):
        db.expire_all()
        if db.get(Asset, "m-2") is None and db.get(Asset, "node-2") is None:
            break
        time.sleep(0.05)
    assert db.get(Asset, "m-2") is None
    assert db.get(Asset, "node-2") is None


def test_uninstall_mother_blocked_when_child_referenced(auth_token, client, db):
    """异常场景：子机被工单引用 → 提交时即拒绝，记录保留。"""
    db.add(_mk_mother("m-2b"))
    db.add(_mk_child("node-2b", "m-2b"))
    db.add(Ticket(
        number="T-2",
        idempotency_key="k-2",
        asset_id="node-2b",
        tenant_id="tenant-default",
        title="t",
        event_id="e-2",
    ))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers/m-2b/uninstall",
        json={"ip": "10.0.0.9", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 409
    assert "tickets" in r.json()["detail"]
    # 记录仍在
    assert db.get(Asset, "m-2b") is not None
    assert db.get(Asset, "node-2b") is not None


def test_uninstall_mother_success(auth_token, client, db, monkeypatch):
    from app.services import mother_deploy as md

    called = {}

    def fake_uninstall(ip, port, username, password, logs):
        called["args"] = (ip, port, username)
        logs.append("远程卸载完成：容器已移除，安装目录（含监控数据）已删除")

    monkeypatch.setattr(md, "uninstall_stack", fake_uninstall)

    db.add(_mk_mother("m-3"))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers/m-3/uninstall",
        json={"ip": "10.0.0.9", "port": 22, "username": "root", "password": "pw"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "uninstalling"

    # 后台线程完成卸载后，台账记录被删除
    for _ in range(60):
        if db.query(Asset).filter(Asset.id == "m-3").first() is None:
            break
        db.expire_all()
        time.sleep(0.05)
    assert db.get(Asset, "m-3") is None
    assert called["args"] == ("10.0.0.9", 22, "root")


def test_uninstall_mother_failure_keeps_record(auth_token, client, db, monkeypatch):
    from app.services import mother_deploy as md

    def fake_uninstall(ip, port, username, password, logs):
        raise RuntimeError("SSH 连接失败")

    monkeypatch.setattr(md, "uninstall_stack", fake_uninstall)

    db.add(_mk_mother("m-4"))
    db.commit()
    r = client.post(
        "/api/v1/assets/mothers/m-4/uninstall",
        json={"ip": "10.0.0.9", "port": 22, "username": "root", "password": "bad"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200

    # 卸载失败：记录保留，deploy 状态回 failed，错误信息可查
    status = ""
    for _ in range(60):
        row = db.get(Asset, "m-4")
        if row is not None:
            status = ((row.extra or {}).get("deploy") or {}).get("status", "")
            if status == "failed":
                break
        db.expire_all()
        time.sleep(0.05)
    assert db.get(Asset, "m-4") is not None
    row = db.get(Asset, "m-4")
    dep = (row.extra or {}).get("deploy") or {}
    assert dep["status"] == "failed"
    assert "卸载失败" in dep["error"]


def test_deploy_detail_endpoint(auth_token, client, db):
    db.add(_mk_mother("m-5"))
    db.commit()
    r = client.get("/api/v1/assets/mothers/m-5/deploy-detail", headers=_h(auth_token))
    assert r.status_code == 200
    d = r.json()
    assert d["ip"] == "10.0.0.9"
    assert d["zabbix"]["web_url"] == "http://10.0.0.9:8081"
    assert d["zabbix"]["api_url"] == "http://10.0.0.9:8081/api_jsonrpc.php"
    # 安装详情接口返回明文账号密码（区别于资产详情接口的脱敏）
    assert d["zabbix"]["user"] == "Admin"
    assert d["zabbix"]["password"] == "zabbix"
    assert d["install"]["dir"] == "~/devops-zabbix"
    assert d["install"]["compose_file"] == "~/devops-zabbix/docker-compose.yml"
    assert "mysql" in d["install"]["containers"]
    assert d["ports"] == {"web": 8081, "trapper": 10051}
    assert d["db"]["mode"] == "bundled"
    assert d["deploy"]["version"] == "5.0.47"

    # 不存在的母机 → 404
    r2 = client.get("/api/v1/assets/mothers/no-such/deploy-detail", headers=_h(auth_token))
    assert r2.status_code == 404
