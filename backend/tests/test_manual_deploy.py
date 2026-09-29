"""人工一键执行白名单预案：白名单注册 / 安全扫描 / 参数注入 / 策略灯 / CMDB 回写。"""

from __future__ import annotations

import time

import pytest

from app.integrations.ansible.safety import UnsafeExecutionError, extra_vars_from, resolve_ansible_playbook
from app.models import Asset


def test_restart_probe_playbook_registered_and_safe(client):
    items = client.get("/api/v1/playbooks").json()["items"]
    assert "ACT-RESTART-PROBE" in {i["id"] for i in items}
    # Playbook 正文必须通过 ad-hoc/shell 模块安全扫描
    path = resolve_ansible_playbook("ACT-RESTART-PROBE")
    assert path.is_file()


def test_extra_vars_inject_probe_name():
    asset = {
        "id": "ast-order-app-01",
        "hostname": "app-01",
        "role": "app",
        "extra": {"service_name": "order-service"},
    }
    from_extra = extra_vars_from("ACT-RESTART-PROBE", asset, {})
    assert from_extra["probe_name"] == "biz-probe"  # 预案默认值
    assert from_extra["service_name"] == "order-service"
    assert from_extra["asset_id"] == "ast-order-app-01"

    from_params = extra_vars_from("ACT-RESTART-PROBE", asset, {"probe_name": "biz-x"})
    assert from_params["probe_name"] == "biz-x"

    # 超出预案允许字段 → 拒绝
    with pytest.raises(UnsafeExecutionError, match="参数超出预案允许字段"):
        extra_vars_from("ACT-RESTART-PROBE", asset, {"nope": 1})
    # 非法标识符 → 拒绝
    with pytest.raises(UnsafeExecutionError, match="非法标识符"):
        extra_vars_from("ACT-RESTART-PROBE", asset, {"probe_name": "a; rm -rf /"})


def test_manual_deploy_green_auto_executes_and_marks_reachable(client, db):
    """低风险白名单预案 → 绿灯自动执行，工单流转 recovered，资产标记可达。"""
    resp = client.post(
        "/api/v1/actions/run",
        json={
            "asset_id": "ast-order-app-01",
            "action_id": "ACT-RESTART-PROBE",
            "params": {"probe_name": "biz-probe"},
            "reason": "探针重启演练",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy_light"] == "green"
    ticket_id = data["ticket"]["id"]

    # 执行（可能在后台线程）完成后工单流转 recovered
    status = ""
    for _ in range(60):
        detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
        status = detail["ticket"]["status"]
        if status == "recovered":
            break
        time.sleep(0.05)
    assert status == "recovered"

    assets = {a["id"]: a for a in client.get("/api/v1/assets").json()["items"]}
    assert assets["ast-order-app-01"]["reachable"] is True


def test_manual_deploy_red_light_rejected(client, db):
    """主机不可达 → 红灯策略拒绝（409），工单不创建执行。"""
    asset = db.get(Asset, "ast-order-app-02")
    asset.reachable = False
    db.commit()

    resp = client.post(
        "/api/v1/actions/run",
        json={"asset_id": "ast-order-app-02", "action_id": "ACT-RESTART-PROBE"},
    )
    assert resp.status_code == 409
    assert "主机不可达" in resp.json()["detail"]


def test_manual_deploy_rejects_non_whitelist_action(client):
    resp = client.post(
        "/api/v1/actions/run",
        json={"asset_id": "ast-order-app-01", "action_id": "ACT-NOT-EXIST"},
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/v1/actions/run",
        json={"asset_id": "ast-not-exist", "action_id": "ACT-RESTART-PROBE"},
    )
    assert resp.status_code == 404


def test_asset_upsert_idempotent(client, db):
    """脚本自动纳管回写 CMDB：创建 + 幂等更新（改 owner 不新建）。"""
    resp = client.post(
        "/api/v1/assets/upsert",
        json={"id": "node-web-01", "hostname": "node-web-01"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "node-web-01"

    # 幂等更新：改 owner 不新建
    resp = client.post(
        "/api/v1/assets/upsert",
        json={"id": "node-web-01", "owner": "ranxiaoying"},
    )
    assert resp.status_code == 200

    items = {a["id"]: a for a in client.get("/api/v1/assets").json()["items"]}
    row = items["node-web-01"]
    assert row["hostname"] == "node-web-01"
    assert row["owner"] == "ranxiaoying"
    assert row["reachable"] is True
    assert sum(1 for k in items if k.startswith("node-web")) == 1

    # 审计链有记录
    audits = client.get("/api/v1/audit").json()["items"]
    assert any(a["event_type"] == "asset_upsert" for a in audits)
