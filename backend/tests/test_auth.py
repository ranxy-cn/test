from tests.conftest import auth_headers, login_token


def test_login_issues_bearer_token(anon_client):
    resp = anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["username"] == "admin"
    assert body["access_token"].count(".") == 2
    assert body["expires_in"] >= 60


def test_login_rejects_bad_password(anon_client):
    resp = anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_api_requires_bearer(anon_client):
    resp = anon_client.get("/api/v1/tickets")
    assert resp.status_code == 401
    status = anon_client.get("/api/v1/status")
    assert status.status_code == 401


def test_health_and_docs_and_login_are_public(anon_client):
    assert anon_client.get("/health").status_code == 200
    assert anon_client.get("/openapi.json").status_code == 200
    assert anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200


def test_webhook_stays_public_with_secret(anon_client):
    resp = anon_client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-auth-public-wh",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["ticket"]["id"]


def test_webhook_still_rejects_missing_secret(anon_client):
    resp = anon_client.post(
        "/api/v1/webhooks/zabbix",
        json={"event_id": "e-no-secret", "asset_id": "ast-order-app-01", "trigger_name": "CPU"},
    )
    assert resp.status_code == 401


def test_bearer_does_not_override_webhook_secret(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-jwt-plus-webhook",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200


def test_expired_or_tampered_token_rejected(anon_client):
    token = login_token(anon_client)
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    resp = anon_client.get("/api/v1/tickets", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
