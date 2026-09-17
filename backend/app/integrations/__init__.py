from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.integrations.ansible.mock import MockPlaybookRunner
from app.integrations.ansible.real import PlaceholderPlaybookRunner
from app.integrations.protocols import PlaybookRunner, VaultClient, ZabbixClient
from app.integrations.vault.http import HttpVaultClient
from app.integrations.vault.mock import MockVaultClient
from app.integrations.zabbix.http import HttpZabbixClient
from app.integrations.zabbix.mock import MockZabbixClient


def _item_mode(settings: Settings, item: str) -> str:
    override = (getattr(settings, f"{item}_mode", "") or "").strip().lower()
    base = (settings.integration_mode or "mock").strip().lower()
    mode = override or base
    return "real" if mode == "real" else "mock"


def _ansible_ready(settings: Settings) -> tuple[bool, str]:
    if not settings.ansible_runner_enabled:
        return False, "ANSIBLE_RUNNER_ENABLED 未打开，保持 mock"
    try:
        import ansible_runner  # type: ignore  # noqa: F401
    except Exception:
        return False, "未安装 ansible-runner，回退 mock（SSH/Runner 占位未启用）"
    return True, ""


def describe_integrations(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ansible_ok, ansible_missing = _ansible_ready(settings)
    items = {}
    for name, ready, missing in (
        ("zabbix", bool(settings.zabbix_url and settings.zabbix_token), "缺少 ZABBIX_URL / ZABBIX_TOKEN"),
        ("ansible", ansible_ok, ansible_missing),
        ("vault", bool(settings.vault_addr and settings.vault_token), "缺少 VAULT_ADDR / VAULT_TOKEN"),
    ):
        requested = _item_mode(settings, name)
        effective = requested if requested == "real" and ready else "mock"
        reason = None
        if requested == "real" and not ready:
            reason = missing
        items[name] = {
            "requested": requested,
            "mode": effective,
            "ready_for_real": ready,
            "fallback_reason": reason,
        }
    return {
        "integration_mode": (settings.integration_mode or "mock").lower(),
        "notify_webhook": bool(settings.notify_webhook_url),
        **items,
    }


def get_zabbix_client() -> ZabbixClient:
    info = describe_integrations()["zabbix"]
    if info["mode"] == "real":
        s = get_settings()
        return HttpZabbixClient(s.zabbix_url, s.zabbix_token)
    return MockZabbixClient()


def get_vault_client() -> VaultClient:
    info = describe_integrations()["vault"]
    if info["mode"] == "real":
        s = get_settings()
        return HttpVaultClient(s.vault_addr, s.vault_token)
    return MockVaultClient()


def get_playbook_runner(vault: VaultClient | None = None) -> PlaybookRunner:
    info = describe_integrations()["ansible"]
    vault = vault or get_vault_client()
    if info["mode"] == "real":
        return PlaceholderPlaybookRunner(vault=vault)
    return MockPlaybookRunner(vault=vault)


def integration_health() -> dict[str, Any]:
    desc = describe_integrations()
    clients = {
        "zabbix": get_zabbix_client().health(),
        "ansible": get_playbook_runner().health(),
        "vault": get_vault_client().health(),
    }
    return {"ok": True, "integrations": desc, "probes": clients}
