"""任务单与资产台账联动：母机/分组/关键字筛选 + 分页 + 资产维度信息 + 分组选项接口。"""

from __future__ import annotations

import pytest

from app.models import Asset, Ticket


@pytest.fixture()
def asset_tree(db):
    """母机 + 两台分组子机 + 一台未分组子机。"""
    mother = Asset(
        id="mo-1",
        kind="mother",
        hostname="mother-1",
        app="devops",
        role="mother",
        owner="张三",
        tenant_id="t-1",
        group="订单组",
        extra={},
    )
    web = Asset(id="ast-web", hostname="web-01", app="订单系统", role="app", owner="张三", mother_id="mo-1", tenant_id="t-1", group="订单组")
    db_snapshot = Asset(id="ast-db", hostname="db-01", app="订单系统", role="db", owner="张三", mother_id="mo-1", tenant_id="t-1", group="中间件")
    lone = Asset(id="ast-lone", hostname="lone-01", app="订单系统", role="app", owner="张三", tenant_id="t-1")
    db.add_all([mother, web, db_snapshot, lone])
    db.flush()
    for i, asset_id in enumerate(["ast-web", "ast-db", "ast-lone", "mo-1"]):
        db.add(
            Ticket(
                number=f"TKT-20260101-000{i + 1}",
                idempotency_key=f"ev-{i}|{asset_id}|v1|PROBLEM",
                event_id=f"ev-{i}",
                asset_id=asset_id,
                tenant_id="t-1",
                title="High CPU utilization",
                status="pending_analysis",
            )
        )
    db.commit()
    return {"mother": mother, "web": web}


def test_tickets_filter_by_mother_and_group(client, asset_tree):
    data = client.get("/api/v1/tickets", params={"mother_id": "mo-1", "page_size": 50}).json()
    assert data["total"] == 3  # 两台子机（web/db）+ 母机自身的告警；未分组且无母机的 lone 不属于该母机
    data = client.get("/api/v1/tickets", params={"mother_id": "mo-1", "group": "订单组", "page_size": 50}).json()
    assert data["total"] == 2  # 子机 web + 母机自身卡（其分组同为 订单组，告警一并命中）
    assert {i["asset_id"] for i in data["items"]} == {"ast-web", "mo-1"}


def test_tickets_keyword_and_pagination(client, asset_tree):
    data = client.get("/api/v1/tickets", params={"keyword": "ast-db"}).json()
    assert data["total"] == 1
    assert data["items"][0]["asset_info"]["hostname"] == "db-01"
    data = client.get("/api/v1/tickets", params={"page": 1, "page_size": 2}).json()
    assert len(data["items"]) == 2 and data["total"] == 4
    data2 = client.get("/api/v1/tickets", params={"page": 2, "page_size": 2}).json()
    assert len(data2["items"]) == 2
    assert {i["id"] for i in data["items"]}.isdisjoint({i["id"] for i in data2["items"]})


def test_mother_group_options_include_custom(db, client, asset_tree):
    mother = asset_tree["mother"]
    mother.extra = {"custom_groups": ["测试组"]}
    db.commit()
    data = client.get("/api/v1/assets/mothers/mo-1/groups").json()
    names = {g["name"]: g["total"] for g in data["items"]}
    assert names["测试组"] == 0  # 自定义空分组也作为可选项
    assert names["订单组"] == 2
