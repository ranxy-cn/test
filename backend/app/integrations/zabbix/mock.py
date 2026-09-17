from __future__ import annotations

from typing import Any

from app.integrations.protocols import ZabbixClient


def mock_metrics(asset_id: str, scenario: str, trigger: str, window_minutes: int = 30) -> dict[str, Any]:
    cpu = 92.4 if "cpu" in trigger.lower() or scenario in {"green", "verify_fail", "cooldown", "lock"} else 41.0
    if scenario == "yellow":
        cpu = 28.0
    if scenario == "red":
        cpu = 67.0
    if scenario == "disk" or "disk" in trigger.lower() or "磁盘" in trigger:
        cpu = 22.0
    return {
        "ref": "metrics:cpu" if scenario != "disk" else "metrics:disk",
        "source": "mock",
        "asset_id": asset_id,
        "window_minutes": window_minutes,
        "cpu_pct": cpu,
        "mem_pct": 71.2,
        "disk_pct": 91.0 if scenario == "disk" or "disk" in trigger.lower() else 38.0,
        "top_process": "java OrderApplication" if scenario != "yellow" else "mysqld",
        "series": [
            {"t": "-25m", "cpu": max(20, cpu - 40)},
            {"t": "-10m", "cpu": max(40, cpu - 10)},
            {"t": "now", "cpu": cpu},
        ],
    }


class MockZabbixClient:
    name = "zabbix-mock"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "mock", "detail": "内存模拟指标/事件"}

    def query_metrics(
        self,
        asset_id: str,
        window_minutes: int = 30,
        *,
        scenario: str = "",
        trigger: str = "",
    ) -> dict[str, Any]:
        return mock_metrics(asset_id, scenario, trigger, window_minutes)

    def query_events(self, asset_id: str, limit: int = 20) -> dict[str, Any]:
        return {
            "source": "mock",
            "asset_id": asset_id,
            "items": [{"event_id": "mock-1", "name": "CPU usage too high", "severity": "high"}],
        }


assert isinstance(MockZabbixClient(), ZabbixClient)
