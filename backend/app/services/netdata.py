"""Netdata Agent REST API 适配层。

只允许访问资产自身登记的 Netdata 地址，不接受前端传入任意 URL，避免把该接口
变成 SSRF 代理。Netdata 不可用时返回结构化离线结果，由调用方决定降级展示。
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.models import Asset


class NetdataConfigError(ValueError):
    """资产 Netdata 地址配置不合法。"""


def _asset_ip(asset: Asset) -> str:
    extra = asset.extra or {}
    provision = extra.get("provision") or {}
    deploy = extra.get("deploy") or {}
    return str(provision.get("ip") or deploy.get("ip") or "").strip()


def _base_url(asset: Asset) -> tuple[str | None, str]:
    settings = get_settings()
    config = (asset.extra or {}).get("netdata") or {}
    if config.get("enabled") is False:
        return None, "资产已关闭 Netdata 采集"
    if not settings.netdata_enabled:
        return None, "系统已关闭 Netdata 采集"

    configured = str(config.get("url") or "").strip()
    if configured:
        return _validate_url(configured), ""

    host = _asset_ip(asset)
    if not host:
        return None, "资产未登记 IP"
    port = int(config.get("port") or settings.netdata_port)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return _validate_url(f"http://{host}:{port}"), ""


def _validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise NetdataConfigError("Netdata URL 必须使用 http 或 https，并包含主机名")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NetdataConfigError("Netdata URL 不允许携带账号、密码、查询参数或片段")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_unspecified or address.is_loopback or address.is_link_local):
        raise NetdataConfigError("Netdata URL 不允许指向本机、未指定或链路本地地址")
    if host in {"localhost", "metadata.google.internal"}:
        raise NetdataConfigError("Netdata URL 不允许使用本地或云元数据地址")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise NetdataConfigError("Netdata 端口不合法")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _request_json(client: httpx.Client, base_url: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.get(f"{base_url}{path}", params=params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Netdata 返回格式不是 JSON 对象")
    return payload


def _chart_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    charts = payload.get("charts") or payload.get("data") or {}
    if isinstance(charts, dict):
        return [(str(key), value if isinstance(value, dict) else {}) for key, value in charts.items()]
    if isinstance(charts, list):
        result = []
        for item in charts:
            if isinstance(item, dict):
                chart_id = str(item.get("id") or item.get("name") or "")
                if chart_id:
                    result.append((chart_id, item))
        return result
    return []


def _pick_chart(items: list[tuple[str, dict[str, Any]]], exact: tuple[str, ...], prefixes: tuple[str, ...] = ()) -> str | None:
    ids = {chart_id for chart_id, _ in items}
    for name in exact:
        if name in ids:
            return name
    for chart_id, meta in items:
        dimensions = " ".join(str(value).lower() for value in (meta.get("dimensions") or []))
        if any(chart_id.startswith(prefix) for prefix in prefixes) and (not dimensions or "receive" in dimensions or "used" in dimensions):
            return chart_id
    for chart_id, _ in items:
        if any(chart_id.startswith(prefix) for prefix in prefixes):
            return chart_id
    return None


def _data_rows(payload: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    names = payload.get("dimension_names") or payload.get("dimensions") or []
    if isinstance(names, dict):
        names = list(names)
    names = [str(name) for name in names]
    rows = payload.get("data") or []
    return names, [row for row in rows if isinstance(row, list) and row]


def _series(payload: dict[str, Any], transform) -> list[dict[str, float | int]]:
    names, rows = _data_rows(payload)
    result = []
    for row in rows:
        try:
            timestamp = int(float(row[0]))
            values = {name.lower(): float(row[index + 1]) for index, name in enumerate(names) if index + 1 < len(row)}
            value = transform(values)
            if value is not None:
                result.append({"t": timestamp, "v": round(max(0.0, float(value)), 2)})
        except (TypeError, ValueError, IndexError):
            continue
    return result


def _latest(series: list[dict[str, float | int]]) -> float | None:
    return float(series[-1]["v"]) if series else None


def _cpu(values: dict[str, float]) -> float | None:
    if not values:
        return None
    idle = sum(value for name, value in values.items() if name in {"idle", "guest_nice", "guest"})
    return 100.0 - idle if "idle" in values else sum(value for name, value in values.items() if name not in {"idle", "guest_nice", "guest"})


def _memory(values: dict[str, float]) -> float | None:
    if not values:
        return None
    used = values.get("used")
    if used is None:
        return None
    available = values.get("available")
    if available is not None and used + available > 0:
        return used / (used + available) * 100
    total = sum(value for name, value in values.items() if name in {"used", "free", "cached", "buffers", "reclaimable"})
    if total <= 0:
        return None
    return used / total * 100


def _disk(values: dict[str, float]) -> float | None:
    used = values.get("used")
    available = values.get("avail", values.get("available", 0.0))
    if used is None or used + available <= 0:
        return None
    return used / (used + available) * 100


def _network_receive(values: dict[str, float]) -> float | None:
    return next((value for name, value in values.items() if name in {"received", "receive", "rx"}), None)


def _network_transmit(values: dict[str, float]) -> float | None:
    return next((value for name, value in values.items() if name in {"sent", "transmit", "tx"}), None)


def _chart_snapshot(client: httpx.Client, base_url: str, chart_id: str | None, minutes: int, transform) -> list[dict[str, float | int]]:
    if not chart_id:
        return []
    payload = _request_json(
        client,
        base_url,
        "/api/v1/data",
        {"chart": chart_id, "after": -(minutes * 60), "points": min(120, max(12, minutes)), "format": "json", "group": "average"},
    )
    return _series(payload, transform)


def collect_snapshot(asset: Asset, minutes: int = 5) -> dict[str, Any]:
    """读取一台资产的实时指标；所有连接错误均转成 offline 结果。"""
    try:
        base_url, reason = _base_url(asset)
    except NetdataConfigError as exc:
        # 非法地址（如回环/链路本地）按未配置降级，不让异常冒泡
        base_url, reason = "", str(exc)
    result: dict[str, Any] = {
        "asset_id": asset.id,
        "hostname": asset.hostname,
        "configured": bool(base_url),
        "online": False,
        "base_url": base_url or "",
        "metrics": {},
        "series": {},
        "charts": [],
        "error": reason,
        "checked_at": datetime.now(timezone.utc),
    }
    if not base_url:
        return result

    try:
        settings = get_settings()
        netdata_config = (asset.extra or {}).get("netdata") or {}
        token = str(netdata_config.get("token") or netdata_config.get("bearer_token") or "").strip()
        headers = {"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}"} if token else {}
        with httpx.Client(timeout=settings.netdata_timeout_seconds, follow_redirects=False, headers=headers) as client:
            info = _request_json(client, base_url, "/api/v1/info")
            charts_payload = _request_json(client, base_url, "/api/v1/charts")
            charts = _chart_items(charts_payload)
            cpu_chart = _pick_chart(charts, ("system.cpu",), ("system.cpu",))
            memory_chart = _pick_chart(charts, ("system.ram",), ("system.ram",))
            disk_chart = _pick_chart(charts, ("disk_space._", "disk_space.root"), ("disk_space.",))
            network_chart = _pick_chart(charts, (), ("net.",))
            cpu = _chart_snapshot(client, base_url, cpu_chart, minutes, _cpu)
            memory = _chart_snapshot(client, base_url, memory_chart, minutes, _memory)
            disk = _chart_snapshot(client, base_url, disk_chart, minutes, _disk)
            receive = _chart_snapshot(client, base_url, network_chart, minutes, _network_receive)
            transmit = _chart_snapshot(client, base_url, network_chart, minutes, _network_transmit)
        result.update(
            online=True,
            error="",
            info={"hostname": info.get("hostname", ""), "os": info.get("os", ""), "version": info.get("version", "")},
            charts=[chart_id for chart_id, _ in charts[:40]],
            metrics={
                "cpu": _latest(cpu),
                "memory": _latest(memory),
                "disk": _latest(disk),
                "network_receive": _latest(receive),
                "network_transmit": _latest(transmit),
            },
            series={"cpu": cpu, "memory": memory, "disk": disk, "network_receive": receive, "network_transmit": transmit},
        )
    except NetdataConfigError as exc:
        result["configured"] = False
        result["base_url"] = ""
        result["error"] = str(exc)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result["error"] = f"Netdata 不可用：{str(exc)[:180]}"
    return result
