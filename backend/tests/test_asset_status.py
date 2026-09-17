from __future__ import annotations

import json

import respx
from httpx import Response

from app.config import get_settings
from app.integrations import clear_integration_probe_cache
from app.models import Asset
from tests.test_zabbix_readonly import ZABBIX_URL, _rpc_ok


def test_assets_list_unchanged_without_metrics(client):
    resp = client.get("/api/v1/assets")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items
    assert "metrics" not in items[0]
    assert {"id", "hostname", "role", "reachable", "db_ok", "zabbix_host", "external_id"} <= set(items[0])


def test_asset_status_requires_login(anon_client):
    resp = anon_client.get("/api/v1/assets/status")
    assert resp.status_code == 401


def test_asset_status_mock_values_and_unmapped(client, db):
    resp = client.get("/api/v1/assets/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated_at"]
    by_id = {row["id"]: row for row in body["items"]}

    mapped = by_id["ast-order-app-01"]["metrics"]
    assert mapped["host_mapped"] is True
    assert mapped["source"] == "mock"
    assert mapped["error"] is None
    assert mapped["cpu_pct"] == 41.0
    assert mapped["mem_pct"] == 71.2
    assert mapped["disk_pct"] == 38.0
    assert mapped["updated_at"]

    unmapped = by_id["ast-lab-unmapped"]["metrics"]
    assert unmapped["host_mapped"] is False
    assert unmapped["cpu_pct"] is None
    assert unmapped["mem_pct"] is None
    assert unmapped["disk_pct"] is None
    assert unmapped["error"] == "未映射"
    assert unmapped["source"] == "unmapped"

    hostname_only = db.get(Asset, "ast-order-unreachable")
    hostname_only.zabbix_host = ""
    hostname_only.external_id = ""
    db.commit()
    again = {row["id"]: row["metrics"] for row in client.get("/api/v1/assets/status").json()["items"]}
    assert again["ast-order-unreachable"]["host_mapped"] is False
    assert again["ast-order-unreachable"]["cpu_pct"] is None
    assert again["ast-order-unreachable"]["error"] == "未映射"


def _status_router():
    hosts = {
        "10001": {"hostid": "10001", "host": "order-app-01", "name": "order-app-01", "status": "0"},
        "10084": {"hostid": "10084", "host": "Zabbix server", "name": "Zabbix server", "status": "0"},
    }

    def handler(request):
        body = json.loads(request.content)
        method = body["method"]
        params = body.get("params") or {}
        if method == "apiinfo.version":
            return _rpc_ok("5.0.41", body.get("id"))
        if method == "host.get":
            hostids = [str(x) for x in (params.get("hostids") or [])]
            names = []
            filt = params.get("filter") or {}
            names.extend(filt.get("host") or [])
            names.extend(filt.get("name") or [])
            if params.get("limit") == 1 and not hostids and not names:
                return _rpc_ok([{"hostid": "10001"}], body.get("id"))
            if hostids:
                found = [hosts[i] for i in hostids if i in hosts]
                return _rpc_ok(found, body.get("id"))
            for name in names:
                for row in hosts.values():
                    if row["host"] == name or row["name"] == name:
                        return _rpc_ok([row], body.get("id"))
            return _rpc_ok([], body.get("id"))
        if method == "item.get":
            return _rpc_ok(
                [
                    {
                        "itemid": "21001",
                        "name": "CPU utilization",
                        "key_": "system.cpu.util",
                        "lastvalue": "81.5",
                        "lastclock": "1710000000",
                        "units": "%",
                        "value_type": "0",
                    },
                    {
                        "itemid": "21002",
                        "name": "Memory utilization",
                        "key_": "vm.memory.utilization",
                        "lastvalue": "76.0",
                        "units": "%",
                        "value_type": "0",
                    },
                    {
                        "itemid": "21003",
                        "name": "Used disk space on / in %",
                        "key_": "vfs.fs.size[/,pused]",
                        "lastvalue": "91.2",
                        "units": "%",
                        "value_type": "0",
                    },
                ],
                body.get("id"),
            )
        if method == "history.get":
            return _rpc_ok([], body.get("id"))
        return Response(400, json={"jsonrpc": "2.0", "error": {"message": f"unexpected {method}"}, "id": 1})

    return handler


@respx.mock
def test_asset_status_real_zabbix_maps_items(client, monkeypatch):
    monkeypatch.setenv("ZABBIX_MODE", "real")
    monkeypatch.setenv("ZABBIX_URL", ZABBIX_URL)
    monkeypatch.setenv("ZABBIX_TOKEN", "tok")
    get_settings.cache_clear()
    clear_integration_probe_cache()
    respx.post(ZABBIX_URL).mock(side_effect=_status_router())
    try:
        body = client.get("/api/v1/assets/status").json()
        by_id = {row["id"]: row["metrics"] for row in body["items"]}
        mapped = by_id["ast-order-app-01"]
        assert mapped["host_mapped"] is True
        assert mapped["source"] == "zabbix-http"
        assert mapped["cpu_pct"] == 81.5
        assert mapped["mem_pct"] == 76.0
        assert mapped["disk_pct"] == 91.2
        assert mapped["error"] is None

        missing = by_id["ast-order-app-02"]
        assert missing["host_mapped"] is False
        assert missing["cpu_pct"] is None
        assert missing["mem_pct"] is None
        assert missing["disk_pct"] is None
        assert "未映射" in (missing["error"] or "")

        unmapped = by_id["ast-lab-unmapped"]
        assert unmapped["host_mapped"] is False
        assert unmapped["cpu_pct"] is None
        assert unmapped["error"] == "未映射"
        methods = [json.loads(call.request.content)["method"] for call in respx.calls]
        assert "history.get" not in methods
        assert "item.get" in methods
    finally:
        monkeypatch.setenv("ZABBIX_MODE", "")
        monkeypatch.delenv("ZABBIX_URL", raising=False)
        monkeypatch.delenv("ZABBIX_TOKEN", raising=False)
        get_settings.cache_clear()
        clear_integration_probe_cache()
