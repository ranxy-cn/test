from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class MockVault:
    """执行器按任务领取短期凭据。密钥永不进入 LLM 上下文。"""

    def issue_lease(self, *, asset_id: str, action_id: str, ttl_seconds: int = 1800) -> dict[str, Any]:
        lease_id = f"lease-{uuid.uuid4().hex[:12]}"
        return {
            "lease_id": lease_id,
            "asset_id": asset_id,
            "action_id": action_id,
            "username": "svc-ops-runner",
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
            "note": "short-lived credential; never exposed to LLM",
        }

    def revoke(self, lease_id: str) -> None:
        return None
