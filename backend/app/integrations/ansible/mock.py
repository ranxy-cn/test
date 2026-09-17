from __future__ import annotations

import time
from typing import Any, Callable

from app.domain.catalog import Playbook
from app.domain.safety import UnsafeExecutionError, validate_tool_call
from app.integrations.protocols import PlaybookRunner, VaultClient
from app.integrations.vault.mock import MockVaultClient
from app.schemas import ToolCall


class MockPlaybookRunner:
    """只运行版本化 Playbook，不接受自由命令。"""

    name = "ansible-mock"

    def __init__(self, vault: VaultClient | None = None):
        self.vault = vault or MockVaultClient()

    def health(self) -> dict[str, Any]:
        return {"ok": True, "mode": "mock", "detail": "模拟 drain/restart/health 步骤"}

    def run(
        self,
        *,
        call: ToolCall,
        playbook: Playbook,
        tenant_id: str,
        asset_tenant_id: str,
        fail_step: str | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        validate_tool_call(
            asset_id=call.asset_id,
            action_id=call.action_id,
            params=call.params,
            tenant_id=tenant_id,
            asset_tenant_id=asset_tenant_id,
        )
        if call.action_id != playbook.id:
            raise UnsafeExecutionError("action_id 与预案不匹配")

        lease = self.vault.issue_lease(asset_id=call.asset_id, action_id=call.action_id)
        steps_out: list[dict[str, Any]] = []
        try:
            for step in playbook.steps:
                record = {
                    "id": step.get("id"),
                    "name": step.get("name"),
                    "status": "ok",
                    "lease_id": lease["lease_id"],
                }
                time.sleep(float(step.get("mock_seconds") or 0))
                if fail_step and step.get("id") == fail_step:
                    record["status"] = "failed"
                    steps_out.append(record)
                    if on_step:
                        on_step(record)
                    return {
                        "ok": False,
                        "playbook_id": playbook.id,
                        "playbook_version": playbook.version,
                        "steps": steps_out,
                        "lease_id": lease["lease_id"],
                    }
                steps_out.append(record)
                if on_step:
                    on_step(record)
            return {
                "ok": True,
                "playbook_id": playbook.id,
                "playbook_version": playbook.version,
                "steps": steps_out,
                "lease_id": lease["lease_id"],
            }
        finally:
            self.vault.revoke(lease["lease_id"])


assert isinstance(MockPlaybookRunner(), PlaybookRunner)
