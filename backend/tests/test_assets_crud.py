"""资产台账管理：分页查询 / 详情 / 删除 / CSV 导出 / CSV 导入。"""

from __future__ import annotations

import csv
import io

from app.models import Asset, MaintenanceWindow, utcnow


def _mk(aid: str, hostname: str = "", **kw) -> Asset:
    return Asset(
        id=aid,
        hostname=hostname or aid,
        app=kw.get("app", "演示App"),
        role=kw.get("role", "app"),
        env=kw.get("env", "prod"),
        owner=kw.get("owner", "张三"),
        tenant_id="tenant-default",
        zabbix_host=kw.get("zabbix_host", ""),
        external_id=kw.get("external_id", ""),
    )


def test_list_assets_paginated_and_filtered(client, db):
    base = client.get("/api/v1/assets", params={"page_size": 1}).json()["total"]  # seed 演示数据基线
    for i in range(25):
        db.add(_mk(f"ast-{i:02d}"))
    db.add(_mk("ast-x-test", env="test", owner="李四"))
    db.commit()

    body = client.get("/api/v1/assets?page=2&page_size=10").json()
    assert body["total"] == base + 26
    assert len(body["items"]) == 10
    assert body["page"] == 2

    # 关键字：命中 hostname / owner（seed 演示数据含李四，故用基线断言）
    body = client.get("/api/v1/assets", params={"keyword": "x-test"}).json()
    assert body["total"] == 1 and body["items"][0]["id"] == "ast-x-test"

    body = client.get("/api/v1/assets", params={"keyword": "ast-2"}).json()
    assert body["total"] == 5  # ast-20 ~ ast-24

    # 环境 + 角色
    body = client.get("/api/v1/assets", params={"env": "test"}).json()
    assert body["total"] == 1
    body = client.get("/api/v1/assets", params={"role": "db"}).json()
    assert body["total"] == 0


def test_get_asset_detail(client, db):
    db.add(_mk("ast-detail-01", zabbix_host="web-01"))
    db.commit()

    row = client.get("/api/v1/assets/ast-detail-01").json()
    assert row["id"] == "ast-detail-01"
    assert row["zabbix_host"] == "web-01"
    assert row["tickets"] == 0 and row["backup_jobs"] == 0
    assert row["extra"] == {}

    assert client.get("/api/v1/assets/no-such").status_code == 404


def test_delete_asset(client, db):
    db.add(_mk("ast-del-01"))
    db.add(_mk("ast-del-02"))
    db.add(MaintenanceWindow(asset_id="ast-del-02", starts_at=utcnow(), ends_at=utcnow(), reason="r"))
    db.commit()

    # 有维护窗口引用 → 409
    r = client.delete("/api/v1/assets/ast-del-02")
    assert r.status_code == 409
    assert "maintenance_windows" in r.json()["detail"]
    assert db.get(Asset, "ast-del-02") is not None

    # 无引用 → 删除成功
    assert client.delete("/api/v1/assets/ast-del-01").status_code == 200
    assert db.get(Asset, "ast-del-01") is None

    assert client.delete("/api/v1/assets/ast-del-01").status_code == 404


def test_export_assets_csv(client, db):
    db.add(_mk("ast-exp-01", zabbix_host="exp-01", external_id="10099", owner="王五"))
    db.commit()

    r = client.get("/api/v1/assets/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    reader = list(csv.DictReader(io.StringIO(text)))
    row = next(r for r in reader if r["id"] == "ast-exp-01")
    assert row["hostname"] == "ast-exp-01"
    assert row["zabbix_host"] == "exp-01" and row["external_id"] == "10099" and row["owner"] == "王五"


def test_import_assets_upsert(client, db):
    db.add(_mk("ast-imp-old", hostname="old-host", owner="旧负责人"))
    db.commit()

    csv_text = (
        "id,hostname,zabbix_host,external_id,app,role,env,owner\n"
        "ast-imp-old,updated-host,,,订单系统,app,prod,新负责人\n"
        "ast-imp-new,new-host,imp-01,10088,缓存系统,cache,prod,赵六\n"
        "bad-row,,,,,,,\n"  # id 缺失
        "no-host,,x,,,,,\n"  # hostname 缺失
    )
    r = client.post(
        "/api/v1/assets/import",
        files={"file": ("assets.csv", csv_text.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 1 and body["updated"] == 1 and body["failed"] == 2

    old = db.get(Asset, "ast-imp-old")
    assert old.hostname == "updated-host" and old.owner == "新负责人"
    new = db.get(Asset, "ast-imp-new")
    assert new.zabbix_host == "imp-01" and new.external_id == "10088" and new.role == "cache"
    assert new.reachable is False  # 导入不假设可达

    # 往返一致：导入的内容可原样导出
    exported = client.get("/api/v1/assets/export").content.decode("utf-8-sig")
    assert "ast-imp-new,new-host,imp-01,10088,缓存系统,cache,prod,赵六" in exported


def test_import_rejects_bad_header(client):
    r = client.post(
        "/api/v1/assets/import",
        files={"file": ("a.csv", b"host,name\nx,y\n", "text/csv")},
    )
    assert r.status_code == 400
    assert "列头" in r.json()["detail"]
