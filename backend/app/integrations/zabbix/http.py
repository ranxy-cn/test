from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.integrations.protocols import ZabbixClient


class HttpZabbixClient:
    """Zabbix JSON-RPC 只读客户端。无凭据时不应被工厂选中。"""

    name = "zabbix-http"

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1, "auth": self.token}
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(self.url, json=payload)
            resp.raise_for_status()
            body = resp.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"]))
        return body.get("result")

    def health(self) -> dict[str, Any]:
        try:
            api = self._rpc("apiinfo.version", {})
            return {"ok": True, "mode": "real", "version": api}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": "real", "detail": str(exc)}

    def query_metrics(
        self,
        asset_id: str,
        window_minutes: int = 30,
        *,
        scenario: str = "",
        trigger: str = "",
    ) -> dict[str, Any]:
        _ = (scenario, trigger)
        result = self._rpc(
            "history.get",
            {"output": "extend", "history": 0, "hostids": [asset_id], "limit": 30},
        )
        return {
            "ref": "metrics:zabbix",
            "source": "zabbix-http",
            "asset_id": asset_id,
            "window_minutes": window_minutes,
            "raw_count": len(result) if isinstance(result, list) else 0,
            "items": result if isinstance(result, list) else [],
        }

    def query_events(self, asset_id: str, limit: int = 20) -> dict[str, Any]:
        result = self._rpc(
            "event.get",
            {"output": "extend", "hostids": [asset_id], "limit": limit, "sortfield": "clock", "sortorder": "DESC"},
        )
        return {"source": "zabbix-http", "asset_id": asset_id, "items": result or []}


def build_http_zabbix() -> HttpZabbixClient:
    s = get_settings()
    return HttpZabbixClient(s.zabbix_url, s.zabbix_token)


assert isinstance(HttpZabbixClient("http://zabbix", "t"), ZabbixClient)
