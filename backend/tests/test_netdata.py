from types import SimpleNamespace

import respx
from httpx import Response

from app.services.netdata import collect_snapshot


def _asset(extra: dict) -> SimpleNamespace:
    return SimpleNamespace(id="ast-netdata", hostname="netdata-01", extra=extra)


@respx.mock
def test_collect_netdata_snapshot_parses_common_charts():
    base = "http://10.0.0.10:19999"
    respx.get(f"{base}/api/v1/info").mock(return_value=Response(200, json={"hostname": "node-01", "version": "v2"}))
    respx.get(f"{base}/api/v1/charts").mock(return_value=Response(200, json={
        "charts": {
            "system.cpu": {"dimensions": ["idle", "user", "system"]},
            "system.ram": {"dimensions": ["used", "available"]},
            "disk_space._": {"dimensions": ["avail", "used"]},
            "net.eth0": {"dimensions": ["received", "sent"]},
        }
    }))

    def data_response(request):
        chart = request.url.params["chart"]
        payloads = {
            "system.cpu": {"dimension_names": ["idle", "user", "system"], "data": [[100, 80, 10, 10]]},
            "system.ram": {"dimension_names": ["used", "available"], "data": [[100, 60, 40]]},
            "disk_space._": {"dimension_names": ["avail", "used"], "data": [[100, 60, 40]]},
            "net.eth0": {"dimension_names": ["received", "sent"], "data": [[100, 123, 456]]},
        }
        return Response(200, json=payloads[chart])

    respx.get(f"{base}/api/v1/data").mock(side_effect=data_response)
    result = collect_snapshot(_asset({"provision": {"ip": "10.0.0.10"}}))

    assert result["online"] is True
    assert result["metrics"]["cpu"] == 20.0
    assert result["metrics"]["memory"] == 60.0
    assert result["metrics"]["disk"] == 40.0
    assert result["metrics"]["network_receive"] == 123.0
    assert result["metrics"]["network_transmit"] == 456.0


def test_collect_netdata_snapshot_rejects_loopback_and_degrades():
    result = collect_snapshot(_asset({"provision": {"ip": "127.0.0.1"}}))

    assert result["configured"] is False
    assert result["online"] is False
    assert "不允许" in result["error"]
