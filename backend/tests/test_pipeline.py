from tests.conftest import auth_headers


def test_green_cpu_auto_recover(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-green-1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage > 85% for 5 minutes",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    ticket = detail["ticket"]
    assert ticket["status"] == "recovered"
    assert ticket["policy_light"] == "green"
    assert ticket["candidate_action_id"] == "ACT-ROLLING-RESTART"
    assert ticket["execution_count"] == 1
    kinds = [e["kind"] for e in detail["events"]]
    assert "evidence_gathered" in kinds
    assert "diagnosis_completed" in kinds
    assert "playbook_step" in kinds
    assert "recovered" in kinds
    logs = ticket["evidence"]["logs"]["lines"]
    assert any("***REDACTED***" in line for line in logs)
    assert not any("prod-admin-key" in line for line in logs)


def test_yellow_requires_approval_then_runs(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-yellow-1",
            "asset_id": "ast-order-db-01",
            "trigger_name": "MySQL replication lag too high",
            "demo_scenario": "yellow",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "pending_approval"
    assert detail["ticket"]["policy_light"] == "yellow"
    approved = client.post(
        f"/api/v1/tickets/{ticket_id}/approve",
        json={"approver": "王五", "comment": "同意主备切换"},
    )
    assert approved.status_code == 200
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "recovered"
    assert any(a["event_type"] == "approval" for a in detail["audit"])


def test_yellow_reject_escalates(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-yellow-reject",
            "asset_id": "ast-order-db-01",
            "trigger_name": "MySQL replication lag too high",
            "demo_scenario": "yellow",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    rejected = client.post(
        f"/api/v1/tickets/{ticket_id}/reject",
        json={"approver": "王五", "comment": "窗口不允许切主"},
    )
    assert rejected.status_code == 200
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "escalated"
    assert detail["ticket"]["execution_count"] == 0


def test_red_unknown_escalates_without_execution(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-red-1",
            "asset_id": "ast-order-app-02",
            "trigger_name": "mystery native crash",
            "demo_scenario": "red",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "escalated"
    assert detail["ticket"]["execution_count"] == 0
    assert detail["ticket"]["policy_light"] == "red"


def test_verify_fail_does_not_loop(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-fail-1",
            "asset_id": "ast-order-job-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "verify_fail",
        },
        headers=auth_headers(),
    )
    ticket_id = resp.json()["ticket"]["id"]
    detail = client.get(f"/api/v1/tickets/{ticket_id}").json()
    assert detail["ticket"]["status"] == "escalated"
    assert detail["ticket"]["execution_count"] == 1
    reason = detail["ticket"]["escalate_reason"] or ""
    assert any(tok in reason for tok in ("循环", "探测", "超时", "失败"))


def test_daily_report_and_employee(client):
    client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "event_id": "evt-report-1",
            "asset_id": "ast-order-app-01",
            "trigger_name": "CPU usage too high",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    emp = client.get("/api/v1/employee/DE-OPS-001").json()
    assert emp["id"] == "DE-OPS-001"
    report = client.get("/api/v1/reports/daily").json()
    assert report["backups"]["status"] == "未检查"
    assert report["completed"] >= 1
    assert "不得将未检查写成成功" in report["backups"]["note"]
