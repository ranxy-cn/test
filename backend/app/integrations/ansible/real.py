from __future__ import annotations

from typing import Any, Callable

from app.config import get_settings
from app.domain.catalog import Playbook
from app.domain.safety import UnsafeExecutionError, validate_tool_call
from app.integrations.protocols import PlaybookRunner, VaultClient
from app.integrations.vault.mock import MockVaultClient
from app.schemas import ToolCall


class PlaceholderPlaybookRunner:
    """真实执行占位：ansible-runner 或 SSH。工厂仅在 ANSIBLE_RUNNER_ENABLED 且已安装 runner 时注入。

    仍只接受白名单 Playbook 的结构化参数，拒绝自由命令。完整 inventory 接入不在二期骨架范围。
    """

    name = "ansible-placeholder"

    def __init__(self, vault: VaultClient | None = None):
        self.vault = vault or MockVaultClient()

    def health(self) -> dict[str, Any]:
        s = get_settings()
        return {
            "ok": False,
            "mode": "real",
            "detail": "需安装 ansible-runner 并设置 ANSIBLE_RUNNER_ENABLED=true；当前为 SSH/Runner 占位",
            "private_data_dir": s.ansible_private_data_dir,
        }

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
        _ = (fail_step, on_step)
        validate_tool_call(
            asset_id=call.asset_id,
            action_id=call.action_id,
            params=call.params,
            tenant_id=tenant_id,
            asset_tenant_id=asset_tenant_id,
        )
        if call.action_id != playbook.id:
            raise UnsafeExecutionError("action_id 与预案不匹配")
        try:
            import ansible_runner  # type: ignore  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "真实 Ansible 未就绪：请安装 ansible-runner，并仅对白名单 Playbook 执行"
            ) from exc
        raise RuntimeError("ansible-runner 已安装，但一期后尚未接入 inventory；拒绝自由命令")


assert isinstance(PlaceholderPlaybookRunner(), PlaybookRunner)
