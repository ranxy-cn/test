from __future__ import annotations

import time
from typing import Any

from app.config import Settings, get_settings
from app.integrations.ansible.mock import MockPlaybookRunner
from app.integrations.ansible.real import AnsiblePlaybookRunner, playbook_binary, runner_importable
from app.integrations.protocols import PlaybookRunner, VaultClient, ZabbixClient
from app.integrations.vault.http import HttpVaultClient
from app.integrations.vault.mock import MockVaultClient
from app.integrations.zabbix.http import HttpZabbixClient, build_http_zabbix
from app.integrations.zabbix.mock import MockZabbixClient

_ZABBIX_PROBE_TTL = 15.0
_zabbix_probe_cache: dict[str, Any] = {"at": 0.0, "result": None}


def clear_integration_probe_cache() -> None:
    _zabbix_probe_cache["at"] = 0.0
    _zabbix_probe_cache["result"] = None


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


def _zabbix_has_creds(settings: Settings) -> bool:
    if not settings.zabbix_url:
        return False
    return bool(settings.zabbix_token or (settings.zabbix_user and settings.zabbix_password))


def _zabbix_missing_reason(settings: Settings) -> str:
    if not settings.zabbix_url:
        return "缺少 ZABBIX_URL"
    if not (settings.zabbix_token or (settings.zabbix_user and settings.zabbix_password)):
        return "缺少 ZABBIX_TOKEN 或 ZABBIX_USER/ZABBIX_PASSWORD"
    return ""


def probe_zabbix(settings: Settings | None = None, *, force: bool = False) -> dict[str, Any]:
    settings = settings or get_settings()
    now = time.time()
    cached = _zabbix_probe_cache.get("result")
    if not force and cached is not None and now - float(_zabbix_probe_cache.get("at") or 0) < _ZABBIX_PROBE_TTL:
        return cached
    client = build_http_zabbix()
    result = client.health()
    _zabbix_probe_cache["at"] = now
    _zabbix_probe_cache["result"] = result
    return result


def describe_integrations(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    ansible_ok, ansible_missing = _ansible_ready(settings)
    zabbix_creds = _zabbix_has_creds(settings)
    items: dict[str, Any] = {}
    for name, ready, missing in (
        ("zabbix", zabbix_creds, _zabbix_missing_reason(settings) or "缺少 Zabbix 凭据"),
        ("ansible", ansible_ok, ansible_missing),
        ("vault", bool(settings.vault_addr and settings.vault_token), "缺少 VAULT_ADDR / VAULT_TOKEN"),
    ):
        requested = _item_mode(settings, name)
        probe = None
        reason = None
        if name == "zabbix" and requested in {"real", "auto"}:
            if not ready:
                effective = "mock"
                reason = missing
            elif requested == "auto":
                probe = probe_zabbix(settings)
                if probe.get("ok"):
                    effective = "real"
                else:
                    effective = "mock"
                    reason = probe.get("last_error") or probe.get("detail") or "Zabbix 健康探测失败，回退 mock"
            else:
                effective = "real"
        elif requested in {"real", "auto"}:
            effective = "real" if ready else "mock"
            if not ready:
                reason = missing
        else:
            effective = "mock"
        items[name] = {
            "requested": requested,
            "mode": effective,
            "ready_for_real": ready,
            "fallback_reason": reason,
            "version": (probe or {}).get("version") if name == "zabbix" else None,
            "latency_ms": (probe or {}).get("latency_ms") if name == "zabbix" else None,
            "last_error": (probe or {}).get("last_error") if name == "zabbix" else reason,
        }
    if items.get("ansible"):
        items["ansible"]["runner_importable"] = runner_importable()
        items["ansible"]["playbook_bin"] = playbook_binary()
    return {
        "integration_mode": (settings.integration_mode or "mock").lower(),
        "notify_webhook": bool(settings.notify_webhook_url),
        **items,
    }


def get_zabbix_client() -> ZabbixClient:
    info = describe_integrations()["zabbix"]
    if info["mode"] == "real":
        return build_http_zabbix()
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
        return AnsiblePlaybookRunner(vault=vault)
    return MockPlaybookRunner(vault=vault)


def integration_health() -> dict[str, Any]:
    desc = describe_integrations()
    clients = {
        "zabbix": get_zabbix_client().health(),
        "ansible": get_playbook_runner().health(),
        "vault": get_vault_client().health(),
    }
    zabbix_desc = desc["zabbix"]
    zabbix_probe = clients["zabbix"]
    zabbix_desc["version"] = zabbix_probe.get("version") or zabbix_desc.get("version")
    zabbix_desc["latency_ms"] = zabbix_probe.get("latency_ms") if zabbix_probe.get("latency_ms") is not None else zabbix_desc.get("latency_ms")
    zabbix_desc["last_error"] = zabbix_probe.get("last_error") or zabbix_desc.get("last_error")
    ansible_desc = desc["ansible"]
    ansible_probe = clients["ansible"]
    ansible_desc["last_error"] = ansible_probe.get("last_error") or ansible_desc.get("fallback_reason") or ansible_desc.get("last_error")
    ansible_desc["version"] = ansible_probe.get("runner") or ("ansible-runner" if ansible_probe.get("runner_importable") else ansible_probe.get("playbook_bin"))
    return {"ok": True, "integrations": desc, "probes": clients}
