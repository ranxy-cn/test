"""母机总览与业务分组：/assets/mother + PATCH /assets/{id}。"""
from __future__ import annotations

from app.models import AnomalyEvent, Asset


def _mk(aid: str, group: str = "") -> Asset:
    return Asset(
        id=aid,
        hostname=aid,
        app="演示App",
        role="app",
        env="prod",
        owner="张三",
        group=group,
        tenant_id="tenant-default",
        extra={"provision": {"ip": "127.0.0.1", "port": 1, "status": "registered"}},
    )


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_mother_overview_groups_children(auth_token, client, db):
    m = _mk("devops-mother-186", group="母机")
    # 母机已部署 Zabbix 栈 → 母机自身也不计入"已接入子机"（子机必须手动添加）
    m.extra = {**(m.extra or {}), "deploy": {"status": "success"}}
    db.add(m)
    db.add(_mk("ast-order", group="订单系统"))
    db.add(_mk("ast-fin", group="财务系统"))
    db.add(_mk("ast-none"))
    db.commit()

    r = client.get("/api/v1/assets/mother", headers=_h(auth_token))
    assert r.status_code == 200
    data = r.json()
    # 子机口径不含母机自身（母机部署完成后子机列表应为空，由手动添加的子机构成）
    rows = {c["id"]: c for c in data["children"]}
    assert "devops-mother-186" not in rows
    assert rows["ast-order"]["id"] == "ast-order"
    names = {g["name"]: g for g in data["groups"]}
    assert names["订单系统"]["total"] == 1
    assert names["财务系统"]["total"] == 1
    assert names[""]["total"] >= 1
    # 母机行带 group 字段
    assert "group" in data["mother"]


def test_patch_asset_update_group(auth_token, client, db):
    db.add(_mk("ast-edit"))
    db.commit()

    r = client.patch(
        "/api/v1/assets/ast-edit",
        json={"group": "订单系统", "owner": "李四"},
        headers=_h(auth_token),
    )
    assert r.status_code == 200
    assert r.json()["group"] == "订单系统"

    row = db.get(Asset, "ast-edit")
    db.refresh(row)
    assert row.group == "订单系统"
    assert row.owner == "李四"

    # 清空分组
    r2 = client.patch("/api/v1/assets/ast-edit", json={"group": ""}, headers=_h(auth_token))
    assert r2.status_code == 200
    assert r2.json()["group"] == ""


def test_patch_asset_hostname_conflict(auth_token, client, db):
    db.add(_mk("ast-h1"))
    db.add(_mk("ast-h2"))
    db.commit()

    r = client.patch("/api/v1/assets/ast-h2", json={"hostname": "ast-h1"}, headers=_h(auth_token))
    assert r.status_code == 409


def test_overview_children_abnormal_count(auth_token, client, db):
    """子机行带 abnormal_count（未恢复告警数），供卡片红色徽标展示。"""
    db.add(_mk("ast-alarm"))
    db.add(_mk("ast-clean"))
    db.commit()
    # ast-alarm：1 条未恢复 + 1 条已恢复；ast-clean：无告警
    db.add(
        AnomalyEvent(
            event_id="ov-1",
            trigger_name="High CPU utilization",
            status="abnormal",
            asset_id="ast-alarm",
            payload={},
        )
    )
    db.add(
        AnomalyEvent(
            event_id="ov-2",
            trigger_name="High memory utilization",
            status="recovered",
            asset_id="ast-alarm",
            payload={},
        )
    )
    db.commit()

    data = client.get("/api/v1/assets/mother", headers=_h(auth_token)).json()
    rows = {c["id"]: c for c in data["children"]}
    assert rows["ast-alarm"]["abnormal_count"] == 1
    assert rows["ast-clean"]["abnormal_count"] == 0
