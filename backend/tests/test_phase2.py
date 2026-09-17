from datetime import timedelta

from app.config import get_settings
from app.integrations import describe_integrations, get_playbook_runner, get_vault_client, get_zabbix_client
from app.integrations.ansible.mock import MockPlaybookRunner
from app.integrations.vault.http import HttpVaultClient
from app.integrations.vault.mock import MockVaultClient
from app.integrations.zabbix.http import HttpZabbixClient
from app.integrations.zabbix.mock import MockZabbixClient
from app.models import ResourceLock, utcnow
from app.services.cooldowns import record_action_failure
from app.services.notify import InboxNotifier, LogNotifier, WebhookNotifier, build_notifiers
from tests.conftest import auth_headers


def test_factory_defaults_to_mock():
    info = describe_integrations()
    assert info["zabbix"]["mode"] == "mock"
    assert info["ansible"]["mode"] == "mock"
    assert info["vault"]["mode"] == "mock"
    assert isinstance(get_zabbix_client(), MockZabbixClient)
    assert isinstance(get_vault_client(), MockVaultClient)
    assert isinstance(get_playbook_runner(), MockPlaybookRunner)


def test_factory_real_without_creds_falls_back(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["zabbix"]["requested"] == "real"
        assert info["zabbix"]["mode"] == "mock"
        assert info["zabbix"]["fallback_reason"]
        assert info["vault"]["mode"] == "mock"
        assert info["ansible"]["mode"] == "mock"
    finally:
        monkeypatch.setenv("INTEGRATION_MODE", "mock")
        get_settings.cache_clear()


def test_resource_lock_mutex(client):
    held = client.post("/api/v1/locks", json={"asset_id": "ast-order-app-01", "ttl_seconds": 120})
    assert held.status_code == 200
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-lock-1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "pending_execution"
    assert detail["ticket"]["execution_count"] == 0
    assert any(e["kind"] == "lock_queued" for e in detail["events"])
    notes = client.get("/api/v1/notifications", params={"ticket_id": ticket_id}).json()["items"]
    assert any(n["kind"] == "lock_queued" for n in notes)

    client.delete("/api/v1/locks/ast-order-app-01")
    retried = client.post(f"/api/v1/tickets/{ticket_id}/retry-execution")
    assert retried.status_code == 200
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "recovered"


def test_notifications_on_green_path(client):
    client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-notify-1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    items = client.get("/api/v1/notifications").json()["items"]
    kinds = {n["kind"] for n in items}
    assert "ticket_created" in kinds
    assert "recovered" in kinds


def test_backup_status_fields(client):
    listed = client.get("/api/v1/backups").json()
    assert listed["jobs"] >= 1
    assert listed["backup_ok_count"] == 0
    assert listed["restore_verified_count"] == 0
    job_id = listed["items"][0]["id"]
    ran = client.post(f"/api/v1/backups/{job_id}/run").json()
    assert ran["backup_ok"] is True
    assert ran["restore_verified"] is None
    after = client.get("/api/v1/backups").json()
    assert after["backup_ok_count"] >= 1
    assert after["restore_verified_count"] == 0
    verified = client.post(f"/api/v1/backups/{job_id}/verify-restore").json()
    assert verified["restore_verified"] is True
    final = client.get("/api/v1/backups").json()
    assert final["restore_verified_count"] >= 1
    report = client.get("/api/v1/reports/daily").json()
    assert report["backups"]["jobs"] >= 1
    assert report["backups"]["backup_ok_count"] >= 1


def test_fail_cooldown_blocks_auto_retry(client, db):
    fail = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-cool-1",
            "asset_id": "ast-order-job-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "verify_fail",
        },
        headers=auth_headers(),
    )
    assert fail.json()["ticket"]["status"] in {"escalated", "pending_analysis"}
    detail = client.get(f"/api/v1/tickets/{fail.json()['ticket']['id']}").json()
    assert detail["ticket"]["status"] == "escalated"
    record_action_failure(db, "ast-order-job-01", "ACT-ROLLING-RESTART", "探测失败")
    db.commit()
    again = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-cool-2",
            "asset_id": "ast-order-job-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    tid = again.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{tid}").json()
    assert detail["ticket"]["status"] == "escalated"
    assert detail["ticket"]["execution_count"] == 0
    assert "冷却" in (detail["ticket"]["escalate_reason"] or "")


def test_status_endpoint(client):
    body = client.get("/api/v1/status").json()
    assert body["integrations"]["zabbix"]["mode"] == "mock"
    assert body["probes"]["zabbix"]["ok"] is True


def test_factory_real_with_creds_selects_http(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "real")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.example/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "zabbix-token")
    monkeypatch.setenv("VAULT_ADDR", "http://vault.example")
    monkeypatch.setenv("VAULT_TOKEN", "vault-token")
    monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "true")
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["zabbix"]["mode"] == "real"
        assert info["vault"]["mode"] == "real"
        assert isinstance(get_zabbix_client(), HttpZabbixClient)
        assert isinstance(get_vault_client(), HttpVaultClient)
        if info["ansible"]["mode"] == "real":
            from app.integrations.ansible.real import AnsiblePlaybookRunner

            assert isinstance(get_playbook_runner(), AnsiblePlaybookRunner)
        else:
            assert isinstance(get_playbook_runner(), MockPlaybookRunner)
            assert info["ansible"]["fallback_reason"]
    finally:
        monkeypatch.setenv("INTEGRATION_MODE", "mock")
        monkeypatch.delenv("ZABBIX_URL", raising=False)
        monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        monkeypatch.delenv("VAULT_TOKEN", raising=False)
        monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "false")
        get_settings.cache_clear()


def test_per_item_mode_override(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "mock")
    monkeypatch.setenv("ZABBIX_MODE", "real")
    monkeypatch.setenv("ZABBIX_URL", "http://zabbix.example/api_jsonrpc.php")
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["zabbix"]["requested"] == "real"
        assert info["zabbix"]["mode"] == "real"
        assert info["ansible"]["mode"] == "mock"
        assert info["vault"]["mode"] == "mock"
        assert isinstance(get_zabbix_client(), HttpZabbixClient)
    finally:
        monkeypatch.setenv("ZABBIX_MODE", "")
        monkeypatch.delenv("ZABBIX_URL", raising=False)
        monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
        get_settings.cache_clear()


def test_expired_lock_is_stolen(client, db):
    db.add(
        ResourceLock(
            asset_id="ast-order-app-01",
            ticket_id=99,
            holder="stale-worker",
            token="dead",
            acquired_at=utcnow() - timedelta(minutes=10),
            heartbeat_at=utcnow() - timedelta(minutes=10),
            expires_at=utcnow() - timedelta(seconds=5),
        )
    )
    db.commit()
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-lock-steal",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "recovered"


def test_notifier_factory_channels():
    channels = {n.channel for n in build_notifiers()}
    assert channels == {"inbox", "log"}
    assert isinstance(InboxNotifier(), InboxNotifier)
    assert isinstance(LogNotifier(), LogNotifier)
    assert WebhookNotifier("http://example.invalid").channel == "webhook"


def test_notifications_pending_approval(client):
    client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-notify-yellow",
            "asset_id": "ast-order-db-01",
            "trigger_name": "MySQL replication lag too high",
            "demo_scenario": "yellow",
        },
        headers=auth_headers(),
    )
    kinds = {n["kind"] for n in client.get("/api/v1/notifications").json()["items"]}
    assert "ticket_created" in kinds
    assert "pending_approval" in kinds
