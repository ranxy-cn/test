"""母机删除（DELETE /assets/{id}）：级联删除名下全部子机台账，纯台账操作不碰服务器。

卸载端点（POST /mothers/{id}/uninstall，同为纯级联删除）的用例见 test_multi_mother.py。
"""
from __future__ import annotations

from app.models import Asset, MaintenanceWindow, Ticket, utcnow


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_mother(aid: str, ip: str = "10.0.0.9") -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="",
        role="mother",
        env="prod",
        owner="张三",
        kind="mother",
        tenant_id="tenant-default",
        reachable=True,
        extra={"provision": {"ip": ip, "port": 22, "status": "registered"}},
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
    db.expire_all()
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
    db.expire_all()
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
    db.add(
        Ticket(
            number="T-1",
            idempotency_key="k-1",
            asset_id="m-1d",
            tenant_id="tenant-default",
            title="t",
            event_id="e-1",
        )
    )
    db.commit()
    r = client.delete("/api/v1/assets/m-1d", headers=_h(auth_token))
    assert r.status_code == 409
    assert "tickets" in r.json()["detail"]
    assert db.get(Asset, "m-1d") is not None
