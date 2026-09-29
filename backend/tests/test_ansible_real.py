from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.domain.safety import UnsafeExecutionError
from app.integrations import describe_integrations, get_playbook_runner
from app.integrations.ansible.mock import MockPlaybookRunner
from app.integrations.ansible.real import AnsiblePlaybookRunner, PlaceholderPlaybookRunner
from app.integrations.ansible.safety import extra_vars_from, resolve_ansible_playbook
from app.schemas import ToolCall


def test_placeholder_alias():
    assert PlaceholderPlaybookRunner is AnsiblePlaybookRunner


def test_resolve_whitelist_playbook():
    path = resolve_ansible_playbook("ACT-ROLLING-RESTART")
    assert path.name == "ACT-ROLLING-RESTART.yml"
    body = path.read_text(encoding="utf-8").lower()
    assert "ansible.builtin.shell" not in body
    assert "ansible.builtin.command" not in body


def test_resolve_rejects_unknown_action():
    with pytest.raises(UnsafeExecutionError):
        resolve_ansible_playbook("ACT-HACK-SHELL")
    with pytest.raises(UnsafeExecutionError):
        resolve_ansible_playbook("../etc/passwd")


def test_extra_vars_reject_non_whitelist_params():
    with pytest.raises(UnsafeExecutionError):
        extra_vars_from("ACT-ROLLING-RESTART", {"id": "ast-order-app-01", "hostname": "order-app-01"}, {"cmd": "reboot"})


def test_factory_auto_without_runner_is_mock(monkeypatch):
    monkeypatch.setenv("ANSIBLE_MODE", "auto")
    monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "true")
    monkeypatch.setattr("app.integrations.runner_importable", lambda: False)
    monkeypatch.setattr("app.integrations.playbook_binary", lambda: None)
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["ansible"]["requested"] == "auto"
        assert info["ansible"]["mode"] == "mock"
        assert info["ansible"]["fallback_reason"]
        assert isinstance(get_playbook_runner(), MockPlaybookRunner)
    finally:
        monkeypatch.setenv("ANSIBLE_MODE", "")
        monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "false")
        get_settings.cache_clear()


def test_factory_real_selects_runner_when_importable(monkeypatch):
    monkeypatch.setenv("ANSIBLE_MODE", "real")
    monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "true")
    monkeypatch.setattr("app.integrations.runner_importable", lambda: True)
    get_settings.cache_clear()
    try:
        info = describe_integrations()
        assert info["ansible"]["requested"] == "real"
        assert info["ansible"]["mode"] == "real"
        assert isinstance(get_playbook_runner(), AnsiblePlaybookRunner)
    finally:
        monkeypatch.setenv("ANSIBLE_MODE", "")
        monkeypatch.setenv("ANSIBLE_RUNNER_ENABLED", "false")
        get_settings.cache_clear()


def test_real_run_playbook_success_mocked(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.ansible.real.run_ansible_job",
        lambda **kwargs: {
            "rc": 0,
            "stdout": "PLAY RECAP ok",
            "stderr": "",
            "runner": "ansible-runner",
            "status": "successful",
        },
    )
    runner = AnsiblePlaybookRunner()
    result = runner.run_playbook(
        "ACT-ROLLING-RESTART",
        {"id": "ast-order-app-01", "hostname": "order-app-01", "extra": {}},
        {"batch_size": 1},
        credential={"lease_id": "lease-test", "private_key": "SHOULD-NOT-LEAK"},
    )
    assert result["ok"] is True
    assert result["rc"] == 0
    assert result["playbook_file"] == "ACT-ROLLING-RESTART.yml"
    assert result["mode"] in {"check", "real"}
    dumped = str(result)
    assert "SHOULD-NOT-LEAK" not in dumped
    assert "private_key" not in result


def test_real_run_playbook_failure_mocked(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.ansible.real.run_ansible_job",
        lambda **kwargs: {"rc": 2, "stdout": "failed", "stderr": "boom", "runner": "ansible-runner", "status": "failed"},
    )
    runner = AnsiblePlaybookRunner()
    result = runner.run_playbook(
        "ACT-CLEAN-TMPLOG",
        {"id": "ast-order-app-01", "hostname": "order-app-01", "extra": {}},
        {"max_age_hours": 24},
    )
    assert result["ok"] is False
    assert result["rc"] == 2
    assert result["stdout_excerpt"]


def test_real_rejects_remote_without_key():
    runner = AnsiblePlaybookRunner()
    result = runner.run_playbook(
        "ACT-RESTART-PROBE",
        {
            "id": "ast-order-app-01",
            "hostname": "order-app-01",
            "extra": {"ansible_host": "10.0.0.8", "ansible_user": "devops"},
        },
        {"probe_name": "biz-probe"},
    )
    assert result["ok"] is False
    assert "私钥" in (result.get("stdout_excerpt") or "")


def test_real_protocol_run_writes_steps(monkeypatch):
    monkeypatch.setattr(
        "app.integrations.ansible.real.run_ansible_job",
        lambda **kwargs: {"rc": 0, "stdout": "ok", "stderr": "", "runner": "ansible-runner", "status": "successful"},
    )
    runner = AnsiblePlaybookRunner()
    from app.domain.catalog import get_playbook

    pb = get_playbook("ACT-ROLLING-RESTART")
    assert pb is not None
    call = ToolCall(asset_id="ast-order-app-01", action_id="ACT-ROLLING-RESTART", params={"batch_size": 1})
    steps: list[dict] = []
    result = runner.run(
        call=call,
        playbook=pb,
        tenant_id="tenant-default",
        asset_tenant_id="tenant-default",
        on_step=steps.append,
        asset={"id": "ast-order-app-01", "hostname": "order-app-01", "extra": {}},
    )
    assert result["ok"] is True
    assert result["lease_id"]
    assert steps


def test_demo_inventory_example_exists():
    root = Path(__file__).resolve().parents[2]
    example = root / "inventory" / "demo.ini.example"
    assert example.is_file()
    assert "ansible_connection=local" in example.read_text(encoding="utf-8")
