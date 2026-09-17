from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.integrations.protocols import VaultClient


class MockVaultClient:
    name = "vault-mock"

    def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "mock", "detail": "内存短凭证，密钥不进入 LLM"}

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


assert isinstance(MockVaultClient(), VaultClient)
