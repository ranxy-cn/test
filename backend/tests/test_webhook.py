from tests.conftest import auth_headers
from app.schemas import ZabbixWebhookIn, flatten_zabbix50_webhook


def test_webhook_rejects_missing_secret(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "e1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
        },
    )
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "e1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
        },
        headers=auth_headers("wrong"),
    )
    assert resp.status_code == 401


def test_webhook_dedup(client):
    payload = {
        "event_id": "evt-dup-1",
        "asset_id": "ast-order-app-01",
        "trigger_name": "CPU usage too high",
        "job_version": "v1",
        "action_type": "PROBLEM",
        "demo_scenario": "green",
    }
    first = client.post("/api/v1/webhooks/zabbix", json=payload, headers=auth_headers())
    assert first.status_code == 200
    t1 = first.json()["ticket"]["number"]
    second = client.post("/api/v1/webhooks/zabbix", json=payload, headers=auth_headers())
    assert second.status_code == 200
    body = second.json()
    assert body["duplicate"] is True
    assert body["ticket"]["number"] == t1


def test_maintenance_window_skip(client):
    payload = {
        "event_id": "evt-maint-1",
        "asset_id": "ast-order-app-03",
        "trigger_name": "CPU usage too high",
    }
    resp = client.post("/api/v1/webhooks/zabbix", json=payload, headers=auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] is True
    assert body["ticket"] is None


def test_flatten_zabbix50_dotted_macros():
    flat = flatten_zabbix50_webhook(
        {
            "EVENT.ID": "12345",
            "HOST.NAME": "Zabbix server",
            "HOST.HOST": "Zabbix server",
            "HOST.ID": "10084",
            "TRIGGER.NAME": "CPU usage too high",
            "EVENT.SEVERITY": "High",
            "EVENT.NSEVERITY": "4",
            "EVENT.VALUE": "1",
            "EVENT.NAME": "CPU usage too high",
            "EVENT.DATE": "2026.09.17",
            "EVENT.TIME": "10:15:32",
        }
    )
    parsed = ZabbixWebhookIn.model_validate(flat)
    assert parsed.event_id == "12345"
    assert parsed.host == "Zabbix server"
    assert parsed.hostid == "10084"
    assert parsed.trigger_name == "CPU usage too high"
    assert parsed.severity == "high"
    assert parsed.nseverity == "4"
    assert parsed.value == "PROBLEM"
    assert parsed.clock == "2026.09.17 10:15:32"


def test_webhook_zabbix50_macros_map_zabbix_server(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "EVENT.ID": "evt-zbx-50-1",
            "HOST.NAME": "Zabbix server",
            "HOST.ID": "10084",
            "TRIGGER.NAME": "CPU usage > 85% for 5 minutes",
            "EVENT.SEVERITY": "High",
            "EVENT.NSEVERITY": "4",
            "EVENT.VALUE": "1",
            "EVENT.NAME": "CPU usage > 85% for 5 minutes",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    ticket = resp.json()["ticket"]
    assert ticket["asset_id"] == "ast-zabbix-server"
    assert ticket["status"] in {"recovered", "pending_approval", "escalated"}
