from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import get_settings
from app.integrations.protocols import ZabbixClient
from app.integrations.zabbix.mapping import has_mapping_hints
from app.integrations.zabbix.methods import ZabbixWriteRejected, assert_readonly_method
from app.integrations.zabbix.mock import mock_metrics

UNMAPPED_NOTE = "未映射，使用 mock/降级"


def _major_version(version: str | None) -> int | None:
    if not version:
        return None
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def _legacy_session_auth(version: str | None) -> bool:
    """Zabbix 5.x/4.x：user.login 用 user+password，session 放 JSON-RPC auth 字段。"""
    major = _major_version(version)
    return major is not None and major < 6


class HttpZabbixClient:
    """Zabbix 6.0/7.0 JSON-RPC 只读客户端。方法名白名单，禁止写操作。"""

    name = "zabbix-http"

    def __init__(
        self,
        url: str,
        token: str = "",
        *,
        username: str = "",
        password: str = "",
        timeout: float = 8.0,
        retries: int = 2,
        verify_ssl: bool = True,
    ):
        self.url = (url or "").rstrip("/")
        self.token = token or ""
        self.username = username or ""
        self.password = password or ""
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.verify_ssl = verify_ssl
        self._session: str | None = None
        self.auth_style: str | None = None
        self.last_error: str | None = None
        self.last_latency_ms: float | None = None
        self.last_version: str | None = None
        self._rpc_id = 0

    def _headers(self, *, use_auth: bool) -> dict[str, str]:
        # 5.0 只认 JSON-RPC auth 字段；Bearer 仅 6.x+ API Token。
        headers = {"Content-Type": "application/json"}
        if use_auth and self.token and not _legacy_session_auth(self.last_version):
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | list | None = None, *, use_auth: bool = True) -> Any:
        assert_readonly_method(method)
        params = params if params is not None else {}
        self._rpc_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._rpc_id}
        if use_auth:
            auth = self.token or self._session
            if auth:
                payload["auth"] = auth
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                    resp = client.post(self.url, json=payload, headers=self._headers(use_auth=use_auth))
                    resp.raise_for_status()
                    body = resp.json()
                if body.get("error"):
                    err = body["error"]
                    raise RuntimeError(err.get("data") or err.get("message") or str(err))
                return body.get("result")
            except ZabbixWriteRejected:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt + 1 >= self.retries:
                    break
                time.sleep(0.05 * (attempt + 1))
        self.last_error = str(last_exc) if last_exc else "unknown"
        raise RuntimeError(self.last_error)

    def _login_param_candidates(self) -> list[dict[str, str]]:
        user = self.username
        password = self.password
        if _legacy_session_auth(self.last_version):
            return [{"user": user, "password": password}]
        if self.last_version:
            return [
                {"username": user, "password": password},
                {"user": user, "password": password},
            ]
        return [
            {"user": user, "password": password},
            {"username": user, "password": password},
        ]

    def _ensure_session(self) -> None:
        if self.token:
            self.auth_style = "token"
            return
        if self._session:
            self.auth_style = "session"
            return
        if not (self.username and self.password):
            return
        last_err: Exception | None = None
        for params in self._login_param_candidates():
            try:
                result = self._rpc("user.login", params, use_auth=False)
                if isinstance(result, str) and result:
                    self._session = result
                    self.auth_style = "session"
                    return
            except ZabbixWriteRejected:
                raise
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        raise RuntimeError(str(last_err) if last_err else "user.login 失败")

    def logout(self) -> None:
        if not self._session:
            return
        try:
            self._rpc("user.logout", [])
        except Exception:
            try:
                self._rpc("user.logout", {})
            except Exception:
                pass
        self._session = None

    def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            version = self._rpc("apiinfo.version", {}, use_auth=False)
            self.last_version = str(version)
            self._ensure_session()
            self._rpc("host.get", {"output": ["hostid"], "limit": 1})
            latency = round((time.perf_counter() - started) * 1000, 1)
            self.last_latency_ms = latency
            self.last_error = None
            return {
                "ok": True,
                "mode": "real",
                "version": version,
                "auth_style": self.auth_style or ("token" if self.token else "session"),
                "latency_ms": latency,
                "last_error": None,
            }
        except Exception as exc:  # noqa: BLE001
            latency = round((time.perf_counter() - started) * 1000, 1)
            self.last_latency_ms = latency
            self.last_error = str(exc)
            return {
                "ok": False,
                "mode": "real",
                "version": self.last_version,
                "latency_ms": latency,
                "last_error": str(exc),
                "detail": str(exc),
            }

    def resolve_host(self, asset_id: str, host_hint: dict[str, Any] | None = None) -> dict[str, Any]:
        hint = dict(host_hint or {})
        hostid = str(hint.get("external_id") or hint.get("webhook_hostid") or "").strip()
        names = [
            str(hint.get("zabbix_host") or "").strip(),
            str(hint.get("webhook_host") or "").strip(),
            str(hint.get("hostname") or "").strip(),
            str(hint.get("webhook_hostname") or "").strip(),
        ]
        names = [n for n in names if n]
        self._ensure_session()
        if hostid:
            rows = self._rpc("host.get", {"output": ["hostid", "host", "name", "status"], "hostids": [hostid]})
            if rows:
                row = rows[0]
                return {"mapped": True, "hostid": str(row.get("hostid")), "host": row.get("host"), "name": row.get("name")}
        if names:
            rows = self._rpc(
                "host.get",
                {"output": ["hostid", "host", "name", "status"], "filter": {"host": names}},
            )
            if not rows:
                rows = self._rpc(
                    "host.get",
                    {"output": ["hostid", "host", "name", "status"], "filter": {"name": names}},
                )
            if rows:
                row = rows[0]
                return {"mapped": True, "hostid": str(row.get("hostid")), "host": row.get("host"), "name": row.get("name")}
        return {
            "mapped": False,
            "asset_id": asset_id,
            "note": UNMAPPED_NOTE,
            "hints": {k: hint.get(k) for k in ("external_id", "zabbix_host", "hostname", "webhook_host", "webhook_hostid")},
        }

    def query_metrics(
        self,
        asset_id: str,
        window_minutes: int = 30,
        *,
        scenario: str = "",
        trigger: str = "",
        host_hint: dict[str, Any] | None = None,
        event_id: str = "",
    ) -> dict[str, Any]:
        _ = event_id
        hint = host_hint or {}
        if not has_mapping_hints(hint):
            data = mock_metrics(asset_id, scenario, trigger, window_minutes)
            data.update({"mapped": False, "note": UNMAPPED_NOTE, "source": "zabbix-http-degraded"})
            return data
        try:
            host = self.resolve_host(asset_id, hint)
            if not host.get("mapped"):
                data = mock_metrics(asset_id, scenario, trigger, window_minutes)
                data.update(
                    {
                        "mapped": False,
                        "note": UNMAPPED_NOTE,
                        "source": "zabbix-http-degraded",
                        "host": host,
                    }
                )
                return data
            hostid = host["hostid"]
            items = self._rpc(
                "item.get",
                {
                    "output": ["itemid", "name", "key_", "lastvalue", "lastclock", "units", "value_type"],
                    "hostids": [hostid],
                    "monitored": True,
                    "limit": 80,
                },
            )
            items = items if isinstance(items, list) else []
            cpu_item = _pick_item(items, ("system.cpu.util", "system.cpu.util[,avg1]", "cpu"))
            mem_item = _pick_item(items, ("vm.memory.utilization", "vm.memory.pavailable", "memory"))
            disk_item = _pick_item(items, ("vfs.fs.size[/,pused]", "vfs.fs.size", "fs.size"))
            time_from = int(time.time()) - int(window_minutes) * 60
            series = _history_series(self, cpu_item, time_from)
            cpu_pct = _num(cpu_item.get("lastvalue") if cpu_item else None)
            mem_pct = _num(mem_item.get("lastvalue") if mem_item else None)
            disk_pct = _num(disk_item.get("lastvalue") if disk_item else None)
            if series and cpu_pct is None:
                cpu_pct = series[-1].get("cpu")
            return {
                "ref": "metrics:zabbix",
                "source": "zabbix-http",
                "asset_id": asset_id,
                "window_minutes": window_minutes,
                "mapped": True,
                "host": host,
                "cpu_pct": cpu_pct,
                "mem_pct": mem_pct,
                "disk_pct": disk_pct,
                "series": series,
                "items_preview": [
                    {"key": i.get("key_"), "lastvalue": i.get("lastvalue"), "units": i.get("units")}
                    for i in items[:8]
                ],
            }
        except ZabbixWriteRejected:
            raise
        except Exception as exc:  # noqa: BLE001
            data = mock_metrics(asset_id, scenario, trigger, window_minutes)
            data.update(
                {
                    "mapped": False,
                    "note": f"{UNMAPPED_NOTE}（{exc}）",
                    "source": "zabbix-http-degraded",
                    "last_error": str(exc),
                }
            )
            self.last_error = str(exc)
            return data

    def query_events(
        self,
        asset_id: str,
        limit: int = 20,
        *,
        host_hint: dict[str, Any] | None = None,
        event_id: str = "",
    ) -> dict[str, Any]:
        hint = host_hint or {}
        if not has_mapping_hints(hint):
            return {
                "source": "zabbix-http-degraded",
                "asset_id": asset_id,
                "mapped": False,
                "note": UNMAPPED_NOTE,
                "items": [],
            }
        try:
            host = self.resolve_host(asset_id, hint)
            if not host.get("mapped"):
                return {
                    "source": "zabbix-http-degraded",
                    "asset_id": asset_id,
                    "mapped": False,
                    "note": UNMAPPED_NOTE,
                    "items": [],
                }
            hostid = host["hostid"]
            problems = self._rpc(
                "problem.get",
                {
                    "output": ["eventid", "name", "severity", "clock", "objectid"],
                    "hostids": [hostid],
                    "recent": True,
                    "sortfield": "eventid",
                    "sortorder": "DESC",
                    "limit": limit,
                },
            )
            event_params: dict[str, Any] = {
                "output": ["eventid", "name", "severity", "clock", "value", "objectid"],
                "hostids": [hostid],
                "sortfield": "clock",
                "sortorder": "DESC",
                "limit": limit,
            }
            if event_id:
                event_params = {
                    "output": ["eventid", "name", "severity", "clock", "value", "objectid"],
                    "eventids": [str(event_id)],
                    "selectHosts": ["hostid", "host"],
                }
            events = self._rpc("event.get", event_params)
            triggers = self._rpc(
                "trigger.get",
                {
                    "output": ["triggerid", "description", "priority", "value"],
                    "hostids": [hostid],
                    "filter": {"value": 1},
                    "limit": limit,
                },
            )
            return {
                "source": "zabbix-http",
                "asset_id": asset_id,
                "mapped": True,
                "host": host,
                "event_id": event_id or None,
                "problems": problems or [],
                "items": events or [],
                "triggers": triggers or [],
            }
        except ZabbixWriteRejected:
            raise
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return {
                "source": "zabbix-http-degraded",
                "asset_id": asset_id,
                "mapped": False,
                "note": f"{UNMAPPED_NOTE}（{exc}）",
                "items": [],
                "last_error": str(exc),
            }


    def list_hosts(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_session()
        rows = self._rpc(
            "host.get",
            {"output": ["hostid", "host", "name", "status"], "limit": limit},
        )
        return rows if isinstance(rows, list) else []

    def list_problems(self, limit: int = 100) -> list[dict[str, Any]]:
        self._ensure_session()
        rows = self._rpc(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock"],
                "recent": True,
                "sortfield": "eventid",
                "sortorder": "DESC",
                "limit": limit,
            },
        )
        return rows if isinstance(rows, list) else []


def _pick_item(items: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any] | None:
    lowered = [(it, str(it.get("key_") or "").lower()) for it in items]
    for want in keys:
        for it, key in lowered:
            if want.lower() == key or want.lower() in key:
                return it
    return items[0] if items else None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_series(client: HttpZabbixClient, item: dict[str, Any] | None, time_from: int) -> list[dict[str, Any]]:
    if not item:
        return []
    value_type = item.get("value_type")
    try:
        history_type = int(value_type) if value_type is not None else 0
    except (TypeError, ValueError):
        history_type = 0
    raw = client._rpc(
        "history.get",
        {
            "output": "extend",
            "history": history_type,
            "itemids": [item["itemid"]],
            "time_from": time_from,
            "sortfield": "clock",
            "sortorder": "ASC",
            "limit": 30,
        },
    )
    if not raw and history_type != 0:
        raw = client._rpc(
            "history.get",
            {
                "output": "extend",
                "history": 0,
                "itemids": [item["itemid"]],
                "time_from": time_from,
                "sortfield": "clock",
                "sortorder": "ASC",
                "limit": 30,
            },
        )
    rows = raw if isinstance(raw, list) else []
    series = []
    for row in rows:
        series.append({"t": row.get("clock"), "cpu": _num(row.get("value"))})
    return series


def build_http_zabbix() -> HttpZabbixClient:
    s = get_settings()
    return HttpZabbixClient(
        s.zabbix_url,
        s.zabbix_token,
        username=s.zabbix_user,
        password=s.zabbix_password,
        timeout=s.zabbix_timeout_seconds,
        retries=s.zabbix_retries,
        verify_ssl=s.zabbix_verify_ssl,
    )


assert isinstance(HttpZabbixClient("http://zabbix", "t"), ZabbixClient)
