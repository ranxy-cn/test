from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from app.config import get_settings
from app.integrations import describe_integrations, get_zabbix_client
from app.integrations.zabbix.http import HttpZabbixClient
from app.integrations.zabbix.methods import READ_ONLY_METHODS, ZabbixWriteRejected, assert_readonly_method
from app.integrations.zabbix.mock import MockZabbixClient
from tests.conftest import auth_headers

ZABBIX_URL = "http://zabbix.test/api_jsonrpc.php"


def test_readonly_whitelist_rejects_writes():
    for method in (
        "host.create",
        "script.execute",
        "configuration.import",
        "event.acknowledge",
        "action.create",
        "host.update",
        "item.delete",
        "trigger.create",
    ):
        with pytest.raises(ZabbixWriteRejected):
            assert_readonly_method(method)
    client = HttpZabbixClient(ZABBIX_URL, "tok")
    with pytest.raises(ZabbixWriteRejected):
        client._rpc("script.execute", {"scriptid": "1", "hostid": "10105"})
    with pytest.raises(ZabbixWriteRejected):
        client._rpc("host.create", {"host": "evil"})
    assert "user.login" in READ_ONLY_METHODS
    assert "user.logout" in READ_ONLY_METHODS
    assert "host.get" in READ_ONLY_METHODS
    assert "history.get" in READ_ONLY_METHODS
    assert "problem.get" in READ_ONLY_METHODS


def _rpc_ok(result, rpc_id=1):
    return Response(200, json={"jsonrpc": "2.0", "result": result, "id": rpc_id})


def _router():
    def handler(request):
        body = json.loads(request.content)
        method = body["method"]
        params = body.get("params") or {}
        if method == "apiinfo.version":
            assert "auth" not in body
            return _rpc_ok("7.0.4", body.get("id"))
        if method == "host.get":
            if params.get("limit") == 1 and "hostids" not in params and "filter" not in params:
                return _rpc_ok([{"hostid": "10001"}], body.get("id"))
            return _rpc_ok(
                [{"hostid": "10001", "host": "order-app-01", "name": "order-app-01", "status": "0"}],
                body.get("id"),
            )
        if method == "item.get":
            return _rpc_ok(
                [
                    {
                        "itemid": "21001",
                        "name": "CPU utilization",
                        "key_": "system.cpu.util",
                        "lastvalue": "91.5",
                        "lastclock": "1710000000",
                        "units": "%",
                        "value_type": "0",
                    },
                    {
                        "itemid": "21002",
                        "name": "Memory utilization",
                        "key_": "vm.memory.utilization",
                        "lastvalue": "70.1",
                        "units": "%",
                        "value_type": "0",
                    },
                ],
                body.get("id"),
            )
        if method == "history.get":
            return _rpc_ok(
                [{"clock": "1710000000", "value": "80.0"}, {"clock": "1710000060", "value": "91.5"}],
                body.get("id"),
            )
        if method == "problem.get":
            return _rpc_ok([{"eventid": "9001", "name": "CPU usage too high", "severity": "4"}], body.get("id"))
        if method == "event.get":
            return _rpc_ok([{"eventid": "9001", "name": "CPU usage too high", "clock": "1710000000"}], body.get("id"))
        if method == "trigger.get":
            return _rpc_ok([{"triggerid": "3001", "description": "CPU usage too high", "value": "1"}], body.get("id"))
        return Response(400, json={"jsonrpc": "2.0", "error": {"message": f"unexpected {method}"}, "id": 1})

    return handler


@respx.mock
def test_http_host_history_problem_success():
    respx.post(ZABBIX_URL).mock(side_effect=_router())
    client = HttpZabbixClient(ZABBIX_URL, "tok")
    health = client.health()
    assert health["ok"] is True
    assert health["version"] == "7.0.4"
    assert health["latency_ms"] is not None
    metrics = client.query_metrics(
        "ast-order-app-01",
        host_hint={"zabbix_host": "order-app-01", "hostname": "order-app-01", "external_id": "10001"},
    )
    assert metrics["mapped"] is True
    assert metrics["source"] == "zabbix-http"
    assert metrics["cpu_pct"] == 91.5
    assert metrics["series"]
    events = client.query_events(
        "ast-order-app-01",
        host_hint={"zabbix_host": "order-app-01"},
        event_id="9001",
    )
    assert events["mapped"] is True
    assert events["problems"]
    assert events["items"]


@respx.mock
def test_http_unmapped_falls_back_to_mock_shape():
    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "host.get":
            return _rpc_ok([])
        if body["method"] == "apiinfo.version":
            return _rpc_ok("6.0.0")
        return _rpc_ok([])

    respx.post(ZABBIX_URL).mock(side_effect=handler)
    client = HttpZabbixClient(ZABBIX_URL, "tok")
    metrics = client.query_metrics(
        "ast-order-app-01",
        scenario="green",
        trigger="CPU usage too high",
        host_hint={"hostname": "missing-host", "zabbix_host": "missing-host"},
    )
    assert metrics["mapped"] is False
    assert "未映射" in metrics["note"]
    assert metrics["cpu_pct"] == 92.4


@respx.mock
def test_http_failure_degrades():
    respx.post(ZABBIX_URL).mock(return_value=Response(500, text="boom"))
    client = HttpZabbixClient(ZABBIX_URL, "tok", retries=1, timeout=1)
    metrics = client.query_metrics(
        "ast-order-app-01",
        scenario="green",
        trigger="CPU",
        host_hint={"hostname": "order-app-01"},
    )
    assert metrics["mapped"] is False
    assert "未映射" in metrics["note"]
    assert metrics["source"] == "zabbix-http-degraded"


def test_factory_auto_without_creds_is_mock(monkeypatch):
    monkeypatch.setenv("ZABBIX_MODE", "auto")
    monkeypatch.delenv("ZABBIX_URL", raising=False)
    monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["zabbix"]["requested"] == "auto"
        assert info["zabbix"]["mode"] == "mock"
        assert isinstance(get_zabbix_client(), MockZabbixClient)
    finally:
        monkeypatch.setenv("ZABBIX_MODE", "")
        get_settings.cache_clear()


@respx.mock
def test_factory_auto_health_success_uses_real(monkeypatch):
    monkeypatch.setenv("ZABBIX_MODE", "auto")
    monkeypatch.setenv("ZABBIX_URL", ZABBIX_URL)
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    get_settings.cache_clear()
    respx.post(ZABBIX_URL).mock(side_effect=_router())
    try:
        info = describe_integrations()
        assert info["zabbix"]["mode"] == "real"
        assert info["zabbix"]["version"] == "7.0.4"
        assert isinstance(get_zabbix_client(), HttpZabbixClient)
    finally:
        monkeypatch.setenv("ZABBIX_MODE", "")
        monkeypatch.delenv("ZABBIX_URL", raising=False)
        monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
        get_settings.cache_clear()


@respx.mock
def test_factory_auto_health_failure_falls_back(monkeypatch):
    monkeypatch.setenv("ZABBIX_MODE", "auto")
    monkeypatch.setenv("ZABBIX_URL", ZABBIX_URL)
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    get_settings.cache_clear()
    respx.post(ZABBIX_URL).mock(return_value=Response(503, text="down"))
    try:
        info = describe_integrations()
        assert info["zabbix"]["requested"] == "auto"
        assert info["zabbix"]["mode"] == "mock"
        assert info["zabbix"]["fallback_reason"]
        assert isinstance(get_zabbix_client(), MockZabbixClient)
    finally:
        monkeypatch.setenv("ZABBIX_MODE", "")
        monkeypatch.delenv("ZABBIX_URL", raising=False)
        monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
        get_settings.cache_clear()


def test_webhook_native_macros_resolve_host(client):
    resp = client.post(
        "/api/v1/webhooks/zabbix",
        json={
            "eventid": "evt-native-1",
            "host": "order-app-01",
            "hostid": "10001",
            "trigger": "CPU usage > 85% for 5 minutes",
            "demo_scenario": "green",
        },
        headers=auth_headers(),
    )
    assert resp.status_code == 200
    ticket = resp.json()["ticket"]
    assert ticket["asset_id"] == "ast-order-app-01"
    assert ticket["status"] == "recovered"
    detail = client.get(f"/api/v1/tickets/{ticket['id']}").json()
    assert "events" in detail["ticket"]["evidence"]
    assert detail["ticket"]["evidence"]["metrics"]["source"] in {"mock", "zabbix-http", "zabbix-http-degraded"}


@respx.mock
def test_zabbix_50_user_login_session_auth():
    """5.0 用 user+password 登录，session 放 JSON-RPC auth，不发 Bearer。"""
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        method = body["method"]
        if method == "apiinfo.version":
            assert "auth" not in body
            return _rpc_ok("5.0.41", body.get("id"))
        if method == "user.login":
            params = body.get("params") or {}
            assert "user" in params
            assert "username" not in params
            assert params["user"] == "Admin"
            assert "auth" not in body
            assert "Authorization" not in request.headers or not str(request.headers.get("Authorization", "")).startswith(
                "Bearer"
            )
            return _rpc_ok("5f0sessionidabcdef", body.get("id"))
        if method == "host.get":
            assert body.get("auth") == "5f0sessionidabcdef"
            return _rpc_ok(
                [{"hostid": "10084", "host": "Zabbix server", "name": "Zabbix server", "status": "0"}],
                body.get("id"),
            )
        if method == "problem.get":
            assert body.get("auth") == "5f0sessionidabcdef"
            return _rpc_ok([{"eventid": "1", "name": "Zabbix agent is not available"}], body.get("id"))
        if method == "user.logout":
            assert body.get("auth") == "5f0sessionidabcdef"
            return _rpc_ok(True, body.get("id"))
        return Response(400, json={"jsonrpc": "2.0", "error": {"message": f"unexpected {method}"}, "id": 1})

    respx.post(ZABBIX_URL).mock(side_effect=handler)
    client = HttpZabbixClient(ZABBIX_URL, token="", username="Admin", password="secret")
    health = client.health()
    assert health["ok"] is True
    assert health["version"] == "5.0.41"
    assert health["auth_style"] == "session"
    hosts = client.list_hosts()
    assert hosts[0]["host"] == "Zabbix server"
    assert len(client.list_problems()) == 1
    client.logout()
    methods = [c["method"] for c in calls]
    assert methods[0] == "apiinfo.version"
    assert "user.login" in methods
    assert "user.logout" in methods
