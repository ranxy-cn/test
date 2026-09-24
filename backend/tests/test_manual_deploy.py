"""人工一键部署 Zabbix Agent：白名单注册 / 安全扫描 / 参数注入 / 策略审批 / CMDB 回写。"""

from __future__ import annotations

from app.integrations.ansible.safety import extra_vars_from, resolve_ansible_playbook
from app.models import Asset


def test_zabbix_deploy_playbook_registered_and_safe(client):
    items = client.get("/api/v1/playbooks").json()["items"]
    assert "ACT-DEPLOY-ZABBIX-AGENT" in {i["id"] for i in items}
    # Playbook 正文必须通过 ad-hoc/shell 模块安全扫描
    path = resolve_ansible_playbook("ACT-DEPLOY-ZABBIX-AGENT")
    assert path.is_file()


def test_extra_vars_inject_zabbix_server():
    asset = {
        "id": "ast-order-app-01",
        "hostname": "app-01",
        "role": "app",
        "extra": {"zabbix_server": "10.0.0.9"},
    }
    from_extra = extra_vars_from("ACT-DEPLOY-ZABBIX-AGENT", asset, {})
    assert from_extra["zabbix_server"] == "10.0.0.9"
    assert from_extra["zabbix_agent_package"] == "zabbix-agent"
    from_params = extra_vars_from("ACT-DEPLOY-ZABBIX-AGENT", asset, {"zabbix_server": "zbx.example.com"})
    assert from_params["zabbix_server"] == "zbx.example.com"


def test_manual_deploy_yellow_approval_then_cmdb_writeback(client, db):
    resp = client.post(
        "/api/v1/actions/run",
        json={
            "asset_id": "ast-order-app-01",
            "action_id": "ACT-DEPLOY-ZABBIX-AGENT",
            "params": {"zabbix_server": "10.0.0.8"},
            "reason": "新机器纳管",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy_light"] == "yellow"
    assert data["ticket"]["status"] == "pending_approval"

    ticket_id = data["ticket"]["id"]
    approved = client.post(f"/api/v1/tickets/{ticket_id}/approve", json={"approver": "admin", "comment": "同意"})
    assert approved.status_code == 200

    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "recovered"

    assets = {a["id"]: a for a in client.get("/api/v1/assets").json()["items"]}
    row = assets["ast-order-app-01"]
    assert row["reachable"] is True
    assert row["zabbix_host"] == row["hostname"]


def test_manual_deploy_red_light_rejected(client, db):
    asset = db.get(Asset, "ast-order-app-02")
    asset.reachable = False
    db.commit()

    resp = client.post(
        "/api/v1/actions/run",
        json={"asset_id": "ast-order-app-02", "action_id": "ACT-DEPLOY-ZABBIX-AGENT"},
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
        json={"asset_id": "ast-not-exist", "action_id": "ACT-DEPLOY-ZABBIX-AGENT"},
    )
    assert resp.status_code == 404


def test_asset_upsert_for_autoreg_nodes(client, db):
    """add-node.sh 接入的机器自动登记进 CMDB：创建 + 幂等更新。"""
    resp = client.post(
        "/api/v1/assets/upsert",
        json={"id": "node-web-01", "hostname": "node-web-01", "zabbix_host": "node-web-01",
              "zabbix_server": "124.221.251.186"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == "node-web-01"

    # 幂等更新：改 owner 不新建
    resp = client.post(
        "/api/v1/assets/upsert",
        json={"id": "node-web-01", "owner": "ranxiaoying", "zabbix_host": "node-web-01"},
    )
    assert resp.status_code == 200

    items = {a["id"]: a for a in client.get("/api/v1/assets").json()["items"]}
    row = items["node-web-01"]
    assert row["zabbix_host"] == "node-web-01"
    assert row["owner"] == "ranxiaoying"
    assert row["reachable"] is True
    assert sum(1 for k in items if k.startswith("node-web")) == 1

    # 审计链有记录
    audits = client.get("/api/v1/audit").json()["items"]
    assert any(a["event_type"] == "asset_upsert" for a in audits)

    db.close()
