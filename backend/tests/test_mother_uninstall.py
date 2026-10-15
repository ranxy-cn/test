"""母机删除（DELETE /assets/{id}）：名下有子机时禁止删除，必须先删完全部子机。

卸载端点（POST /mothers/{id}/uninstall，同规则）的用例见 test_multi_mother.py。
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


def test_delete_mother_blocked_with_children(auth_token, client, db):
    """删除限制：母机名下有子机 → 400 拒绝，母机与子机均保留。"""
    db.add(_mk_mother("m-1"))
    db.add(_mk_child("node-1", "m-1"))
    db.add(_mk_child("node-1b", "m-1"))
    db.commit()
    r = client.delete("/api/v1/assets/m-1", headers=_h(auth_token))
    assert r.status_code == 400
    assert "先删除" in r.json()["detail"] and "2 台子机" in r.json()["detail"]
    db.expire_all()
    assert db.get(Asset, "m-1") is not None
    assert db.get(Asset, "node-1") is not None
    assert db.get(Asset, "node-1b") is not None


def test_delete_mother_after_children_removed(auth_token, client, db):
    """完整流程：逐台删除子机后，母机才允许删除，且不留悬空子机。"""
    db.add(_mk_mother("m-2"))
    db.add(_mk_child("node-2", "m-2"))
    db.add(_mk_child("node-2b", "m-2"))
    # 其他母机及其子机不受影响
    db.add(_mk_mother("m-other", ip="10.0.0.8"))
    db.add(_mk_child("node-other", "m-other"))
    db.commit()

    # 有子机时删除母机被拒
    r0 = client.delete("/api/v1/assets/m-2", headers=_h(auth_token))
    assert r0.status_code == 400

    # 逐台删子机
    for cid in ("node-2", "node-2b"):
        r = client.post(f"/api/v1/assets/{cid}/remove", headers=_h(auth_token), json={"uninstall": False})
        assert r.status_code == 200
    db.expire_all()
    assert db.get(Asset, "node-2") is None and db.get(Asset, "node-2b") is None

    # 子机删净后母机可删
    r1 = client.delete("/api/v1/assets/m-2", headers=_h(auth_token))
    assert r1.status_code == 200
    assert r1.json()["cascade_children"] == []
    db.expire_all()
    assert db.get(Asset, "m-2") is None
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
    """异常场景：子机被维护窗口引用 → 子机自身删除被拒（409），母机因有子机同样被拒。"""
    db.add(_mk_mother("m-1c"))
    db.add(_mk_child("node-1c", "m-1c"))
    db.add(MaintenanceWindow(asset_id="node-1c", starts_at=utcnow(), ends_at=utcnow(), reason="r"))
    db.commit()
    r = client.delete("/api/v1/assets/m-1c", headers=_h(auth_token))
    assert r.status_code == 400  # 有子机 → 母机直接拒绝（不再进入级联引用检查）
    # 子机自身删除被引用拦截
    r2 = client.post("/api/v1/assets/node-1c/remove", headers=_h(auth_token), json={"uninstall": False})
    assert r2.status_code == 409
    db.expire_all()
    assert db.get(Asset, "m-1c") is not None
    assert db.get(Asset, "node-1c") is not None


def test_delete_mother_blocked_when_mother_referenced(auth_token, client, db):
    """异常场景：无子机母机自身被工单引用 → 拒绝。"""
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
