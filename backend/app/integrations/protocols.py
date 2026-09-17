from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domain.catalog import Playbook
from app.schemas import ToolCall


@runtime_checkable
class ZabbixClient(Protocol):
    name: str

    def health(self) -> dict[str, Any]: ...

    def query_metrics(
        self,
        asset_id: str,
        window_minutes: int = 30,
        *,
        scenario: str = "",
        trigger: str = "",
    ) -> dict[str, Any]: ...

    def query_events(self, asset_id: str, limit: int = 20) -> dict[str, Any]: ...


@runtime_checkable
class VaultClient(Protocol):
    name: str

    def health(self) -> dict[str, Any]: ...

    def issue_lease(self, *, asset_id: str, action_id: str, ttl_seconds: int = 1800) -> dict[str, Any]: ...

    def revoke(self, lease_id: str) -> None: ...


@runtime_checkable
class PlaybookRunner(Protocol):
    name: str

    def health(self) -> dict[str, Any]: ...

    def run(
        self,
        *,
        call: ToolCall,
        playbook: Playbook,
        tenant_id: str,
        asset_tenant_id: str,
        fail_step: str | None = None,
        on_step: Any = None,
    ) -> dict[str, Any]: ...
