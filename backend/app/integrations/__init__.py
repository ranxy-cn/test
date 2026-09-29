from __future__ import annotations

from typing import Any

from app.config import Settings, get_settings
from app.integrations.ansible.mock import MockPlaybookRunner
from app.integrations.ansible.real import AnsiblePlaybookRunner, playbook_binary, runner_importable
from app.integrations.protocols import PlaybookRunner, VaultClient
from app.integrations.vault.http import HttpVaultClient
from app.integrations.vault.mock import MockVaultClient


def _item_mode(settings: Settings, item: str) -> str:
    override = (getattr(settings, f"{item}_mode", "") or "").strip().lower()
    base = (settings.integration_mode or "mock").strip().lower()
    mode = override or base
    if mode in {"real", "auto", "mock"}:
        return mode
    return "mock"


def _ansible_enabled(settings: Settings) -> bool:
    if settings.ansible_runner_enabled:
        return True
    override = (settings.ansible_mode or "").strip().lower()
    return override in {"real", "auto"}


def _ansible_ready(settings: Settings) -> tuple[bool, str]:
    if not _ansible_enabled(settings):
        return False, "ANSIBLE_RUNNER_ENABLED 未打开，保持 mock"
    requested = _item_mode(settings, "ansible")
    has_runner = runner_importable()
    binary = playbook_binary()
    if requested == "auto":
        if has_runner:
            return True, ""
        return False, "auto 需要可导入 ansible-runner，回退 mock"
    if has_runner or binary:
        return True, ""
    return False, "未安装 ansible-runner / ansible-playbook，回退 mock"


def describe_integrations(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ansible_ok, ansible_missing = _ansible_ready(settings)
    items: dict[str, Any] = {}
    for name, ready, missing in (
        ("ansible", ansible_ok, ansible_missing),
        ("vault", bool(settings.vault_addr and settings.vault_token), "缺少 VAULT_ADDR / VAULT_TOKEN"),
    ):
        requested = _item_mode(settings, name)
        effective = "real" if (requested in {"real", "auto"} and ready) else "mock"
        reason = missing if requested in {"real", "auto"} and not ready else None
        items[name] = {
            "requested": requested,
            "mode": effective,
            "ready_for_real": ready,
            "fallback_reason": reason,
        }
    if items.get("ansible"):
        items["ansible"]["runner_importable"] = runner_importable()
        items["ansible"]["playbook_bin"] = playbook_binary()
    return {
        "integration_mode": (settings.integration_mode or "mock").lower(),
        "notify_webhook": bool(settings.notify_webhook_url),
        "stress_tools_enabled": bool(settings.stress_tools_enabled),
        **items,
    }


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
        return AnsiblePlaybookRunner(vault=vault)
    return MockPlaybookRunner(vault=vault)


def integration_health() -> dict[str, Any]:
    desc = describe_integrations()
    clients = {
        "ansible": get_playbook_runner().health(),
        "vault": get_vault_client().health(),
    }
    ansible_desc = desc["ansible"]
    ansible_probe = clients["ansible"]
    ansible_desc["last_error"] = ansible_probe.get("last_error") or ansible_desc.get("fallback_reason") or ansible_desc.get("last_error")
    ansible_desc["version"] = ansible_probe.get("runner") or ("ansible-runner" if ansible_probe.get("runner_importable") else ansible_probe.get("playbook_bin"))
    return {"ok": True, "integrations": desc, "probes": clients}
