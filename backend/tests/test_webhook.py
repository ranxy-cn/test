from tests.conftest import auth_headers


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
