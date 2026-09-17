from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.domain.catalog import Playbook, get_playbook
from app.domain.safety import UnsafeExecutionError, validate_tool_call
from app.integrations.ansible.inventory import fallback_demo_inventory, inventory_from_asset, _asset_field
from app.integrations.ansible.safety import (
    clip_log,
    extra_vars_from,
    resolve_ansible_playbook,
    sanitize_execution_result,
)
from app.integrations.protocols import PlaybookRunner, VaultClient
from app.integrations.vault.mock import MockVaultClient
from app.schemas import ToolCall


def runner_importable() -> bool:
    try:
        import ansible_runner  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def playbook_binary() -> str | None:
    return shutil.which("ansible-playbook")


def run_ansible_job(
    *,
    playbook_path: Path,
    inventory_text: str,
    extravars: dict[str, Any],
    timeout: int,
    check: bool,
    private_data_dir: str,
    roles_path: str = "",
    ssh_key_file: str = "",
    host_key_checking: bool = True,
) -> dict[str, Any]:
    """执行白名单 playbook。优先 ansible-runner，否则 ansible-playbook 子进程。"""
    work = Path(private_data_dir) / f"run-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        inv_path = work / "inventory.ini"
        inv_path.write_text(inventory_text, encoding="utf-8")
        project = work / "project"
        project.mkdir(exist_ok=True)
        dest_play = project / playbook_path.name
        dest_play.write_text(playbook_path.read_text(encoding="utf-8"), encoding="utf-8")
        envvars = {
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_HOST_KEY_CHECKING": "True" if host_key_checking else "False",
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
        }
        if roles_path:
            envvars["ANSIBLE_ROLES_PATH"] = roles_path
        if ssh_key_file:
            envvars["ANSIBLE_PRIVATE_KEY_FILE"] = ssh_key_file
        if runner_importable():
            return _run_with_runner(
                work=work,
                playbook_name=playbook_path.name,
                inventory_path=inv_path,
                extravars=extravars,
                envvars=envvars,
                timeout=timeout,
                check=check,
            )
        binary = playbook_binary()
        if not binary:
            raise RuntimeError("未安装 ansible-runner / ansible-playbook")
        return _run_with_subprocess(
            binary=binary,
            playbook_path=dest_play,
            inventory_path=inv_path,
            extravars=extravars,
            envvars=envvars,
            timeout=timeout,
            check=check,
            ssh_key_file=ssh_key_file,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_with_runner(
    *,
    work: Path,
    playbook_name: str,
    inventory_path: Path,
    extravars: dict[str, Any],
    envvars: dict[str, str],
    timeout: int,
    check: bool,
) -> dict[str, Any]:
    import ansible_runner  # type: ignore

    result = ansible_runner.run(
        private_data_dir=str(work),
        playbook=playbook_name,
        inventory=str(inventory_path),
        extravars=extravars,
        envvars=envvars,
        quiet=True,
        timeout=int(timeout),
        cmdline="--check" if check else None,
    )
    rc = int(result.rc if result.rc is not None else 1)
    stdout = ""
    stderr = ""
    try:
        stdout = result.stdout.read() if result.stdout else ""
    except Exception:
        stdout = str(getattr(result, "stdout", "") or "")
    try:
        stderr = result.stderr.read() if result.stderr else ""
    except Exception:
        stderr = str(getattr(result, "stderr", "") or "")
    status = getattr(result, "status", "") or ""
    return {"rc": rc, "stdout": stdout, "stderr": stderr, "runner": "ansible-runner", "status": status}


def _run_with_subprocess(
    *,
    binary: str,
    playbook_path: Path,
    inventory_path: Path,
    extravars: dict[str, Any],
    envvars: dict[str, str],
    timeout: int,
    check: bool,
    ssh_key_file: str,
) -> dict[str, Any]:
    import os
    import subprocess

    extra_file = playbook_path.parent.parent / "extravars.json"
    extra_file.write_text(json.dumps(extravars, ensure_ascii=False), encoding="utf-8")
    cmd = [binary, "-i", str(inventory_path), str(playbook_path), "-e", f"@{extra_file}"]
    if check:
        cmd.append("--check")
    if ssh_key_file:
        cmd.extend(["--private-key", ssh_key_file])
    merged_env = dict(os.environ)
    merged_env.update(envvars)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=int(timeout),
            env=merged_env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "rc": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "") + f"\nansible-playbook 超时 {timeout}s",
            "runner": "ansible-playbook",
            "status": "timeout",
        }
    return {
        "rc": int(proc.returncode),
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "runner": "ansible-playbook",
        "status": "successful" if proc.returncode == 0 else "failed",
    }


class AnsiblePlaybookRunner:
    """真实 Ansible：只跑 playbooks/ansible 下白名单 YAML，拒绝任意 shell。"""

    name = "ansible-runner"

    def __init__(self, vault: VaultClient | None = None):
        self.vault = vault or MockVaultClient()
        self.last_error: str | None = None

    def health(self) -> dict[str, Any]:
        s = get_settings()
        has_runner = runner_importable()
        binary = playbook_binary()
        inventory = (s.ansible_inventory or "").strip()
        ok = has_runner or bool(binary)
        detail = "ansible-runner 可用" if has_runner else (
            f"ansible-playbook={binary}" if binary else "未安装 ansible-runner / ansible-playbook"
        )
        if not ok:
            self.last_error = detail
        return {
            "ok": ok,
            "mode": "real",
            "detail": detail,
            "runner_importable": has_runner,
            "playbook_bin": binary,
            "inventory": inventory or None,
            "check_mode": bool(s.ansible_check_mode),
            "private_data_dir": s.ansible_private_data_dir,
            "last_error": None if ok else detail,
        }

    def run_playbook(
        self,
        action_id: str,
        asset: Any,
        params: dict[str, Any] | None = None,
        credential: dict[str, Any] | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        play_file = resolve_ansible_playbook(action_id)
        extra = extra_vars_from(action_id, asset, params or {})
        pb_meta = get_playbook(action_id)
        credential = dict(credential or {})
        credential.pop("private_key", None)
        credential.pop("secret", None)
        credential.pop("password", None)
        ssh_key = (credential.get("private_key_path") or settings.ansible_ssh_private_key_file or "").strip()
        if ssh_key and not Path(ssh_key).is_file():
            raise UnsafeExecutionError("ANSIBLE_SSH_PRIVATE_KEY_FILE 不是可读文件")

        try:
            inventory_text, inv_source = inventory_from_asset(asset)
        except FileNotFoundError as exc:
            self.last_error = str(exc)
            return _failed_result(action_id, play_file, str(exc), inventory_source="missing")

        check = bool(settings.ansible_check_mode)
        if inv_source in {"demo-example", "demo-builtin"}:
            check = True
        remote_host = bool(_asset_field(asset, "ansible_host", "ip", "ssh_host"))
        local = "ansible_connection=local" in inventory_text
        if not ssh_key and not local and not check:
            if remote_host or inv_source == "file":
                msg = "缺少 SSH 私钥（Vault 短凭证路径或 ANSIBLE_SSH_PRIVATE_KEY_FILE），拒绝连接真实主机"
                self.last_error = msg
                return _failed_result(action_id, play_file, msg, inventory_source=inv_source)
            inventory_text, inv_source = fallback_demo_inventory()
            check = True

        if on_step:
            on_step({"id": "prepare", "name": f"准备 {play_file.name}", "status": "ok"})

        started = time.perf_counter()
        try:
            raw = run_ansible_job(
                playbook_path=play_file,
                inventory_text=inventory_text,
                extravars=extra,
                timeout=int(settings.ansible_timeout_seconds),
                check=check,
                private_data_dir=settings.ansible_private_data_dir,
                roles_path=settings.ansible_roles_path,
                ssh_key_file=ssh_key,
                host_key_checking=settings.ansible_host_key_checking,
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            if on_step:
                on_step({"id": "ansible", "name": "Ansible 调用失败", "status": "failed"})
            return _failed_result(action_id, play_file, str(exc), inventory_source=inv_source)

        rc_raw = raw.get("rc")
        rc = int(rc_raw) if rc_raw is not None else 1
        ok = rc == 0
        excerpt = clip_log("\n".join(part for part in (raw.get("stdout") or "", raw.get("stderr") or "") if part))
        if on_step:
            on_step(
                {
                    "id": "ansible",
                    "name": play_file.name,
                    "status": "ok" if ok else "failed",
                    "rc": rc,
                }
            )
        result = {
            "ok": ok,
            "playbook_id": action_id,
            "playbook_file": play_file.name,
            "playbook_version": pb_meta.version if pb_meta else "",
            "steps": [
                {"id": "ansible", "name": play_file.name, "status": "ok" if ok else "failed", "rc": rc},
            ],
            "rc": rc,
            "stdout_excerpt": excerpt,
            "runner": raw.get("runner") or "ansible-runner",
            "mode": "check" if check else "real",
            "inventory_source": inv_source,
            "lease_id": credential.get("lease_id"),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "status": raw.get("status"),
        }
        if not ok:
            self.last_error = excerpt[-400:] or f"ansible rc={rc}"
        return sanitize_execution_result(result)

    def run(
        self,
        *,
        call: ToolCall,
        playbook: Playbook,
        tenant_id: str,
        asset_tenant_id: str,
        fail_step: str | None = None,
        on_step: Callable[[dict[str, Any]], None] | None = None,
        asset: Any = None,
    ) -> dict[str, Any]:
        _ = fail_step
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
        credential = {
            "lease_id": lease.get("lease_id"),
            "username": lease.get("username"),
            "private_key_path": lease.get("private_key_path"),
        }
        try:
            result = self.run_playbook(
                call.action_id,
                asset,
                call.params,
                credential=credential,
                on_step=on_step,
            )
            result["lease_id"] = lease.get("lease_id")
            result["playbook_version"] = playbook.version
            return sanitize_execution_result(result)
        finally:
            self.vault.revoke(lease.get("lease_id") or "")


def _failed_result(action_id: str, play_file: Path, error: str, inventory_source: str) -> dict[str, Any]:
    return sanitize_execution_result(
        {
            "ok": False,
            "playbook_id": action_id,
            "playbook_file": play_file.name,
            "steps": [{"id": "ansible", "name": play_file.name, "status": "failed"}],
            "rc": 1,
            "stdout_excerpt": clip_log(error),
            "runner": "ansible-runner",
            "mode": "real",
            "inventory_source": inventory_source,
            "lease_id": None,
        }
    )


PlaceholderPlaybookRunner = AnsiblePlaybookRunner

assert isinstance(AnsiblePlaybookRunner(), PlaybookRunner)
