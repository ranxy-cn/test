from __future__ import annotations

import time
from typing import Any

from app.config import get_settings


class BusinessProbeVerifier:
    """独立业务探测：连续 N 次通过才算数，失败立即停止。"""

    def probe_once(self, *, asset_id: str, scenario: str) -> dict[str, Any]:
        if scenario == "verify_fail":
            return {"ok": False, "login": "fail", "query": "fail", "reason": "业务查询超时"}
        return {"ok": True, "login": "ok", "query": "ok"}

    def verify(self, *, asset_id: str, scenario: str) -> dict[str, Any]:
        settings = get_settings()
        required = settings.probe_required_passes
        results: list[dict[str, Any]] = []
        consecutive = 0
        for i in range(required):
            item = self.probe_once(asset_id=asset_id, scenario=scenario)
            item["seq"] = i + 1
            results.append(item)
            if not item["ok"]:
                return {
                    "ok": False,
                    "consecutive_passes": consecutive,
                    "required": required,
                    "probes": results,
                    "reason": item.get("reason") or "探测失败",
                }
            consecutive += 1
            if settings.probe_interval_seconds > 0 and i < required - 1:
                time.sleep(settings.probe_interval_seconds)

        observation = []
        if settings.observation_seconds > 0:
            time.sleep(settings.observation_seconds)
            rec = self.probe_once(asset_id=asset_id, scenario=scenario)
            rec["phase"] = "observation"
            observation.append(rec)
            if not rec["ok"]:
                return {
                    "ok": False,
                    "consecutive_passes": consecutive,
                    "required": required,
                    "probes": results,
                    "observation": observation,
                    "reason": "观察期复发，停止且不循环重启",
                    "recurrence": True,
                }
        return {
            "ok": True,
            "consecutive_passes": consecutive,
            "required": required,
            "probes": results,
            "observation": observation,
        }
