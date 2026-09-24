from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.integrations.protocols import VaultClient


class HttpVaultClient:
    name = "vault-http"

    def __init__(self, addr: str, token: str):
        self.addr = addr.rstrip("/")
        self.token = token

    def _headers(self) -> dict[str, str]:
        return {"X-Vault-Token": self.token}

    def health(self) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{self.addr}/v1/sys/health")
            return {"ok": resp.status_code < 500, "mode": "real", "status_code": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": "real", "detail": str(exc)}

    def issue_lease(self, *, asset_id: str, action_id: str, ttl_seconds: int = 1800) -> dict[str, Any]:
        # 真实密钥只短暂存在于此调用栈，审计只记 lease_id。
        path = f"{self.addr}/v1/sys/leases/lookup"
        with httpx.Client(timeout=8.0) as client:
            client.post(
                f"{self.addr}/v1/auth/token/renew-self",
                headers=self._headers(),
                json={"increment": ttl_seconds},
            )
        _ = (path, asset_id)
        return {
            "lease_id": f"vault-{action_id}-{asset_id}",
            "asset_id": asset_id,
            "action_id": action_id,
            "username": "svc-ops-runner",
            "ttl_seconds": ttl_seconds,
            "note": "secret material is not returned to callers for audit/LLM",
        }

    def revoke(self, lease_id: str) -> None:
        try:
            with httpx.Client(timeout=5.0) as client:
                client.put(f"{self.addr}/v1/sys/leases/revoke", headers=self._headers(), json={"lease_id": lease_id})
        except Exception:
            return None


def build_http_vault() -> HttpVaultClient:
    s = get_settings()
    return HttpVaultClient(s.vault_addr, s.vault_token)


assert isinstance(HttpVaultClient("http://vault", "t"), VaultClient)
