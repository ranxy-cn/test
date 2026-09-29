"""Ansible 执行安全：白名单 play 路径、禁止 ad-hoc/shell、日志脱敏与截断。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.domain.catalog import get_playbook
from app.domain.safety import UnsafeExecutionError
from app.schemas import ACTION_ID_RE

FORBIDDEN_ANSIBLE_TOKENS = (
    "ansible.builtin.shell",
    "ansible.builtin.command",
    "ansible.builtin.raw",
    "ansible.builtin.script",
    "ansible.windows.win_shell",
    "ansible.windows.win_command",
    "\nshell:",
    "\ncommand:",
    "\nraw:",
    "\nscript:",
    " shell:",
    " command:",
    " raw:",
    " script:",
)

SAFE_IDENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_LOG_CHARS = 8000

_REDACT = [
    (re.compile(r"(?i)(password\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(ansible_ssh_pass(word)?\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)BEGIN [A-Z ]*PRIVATE KEY[\s\S]+?END [A-Z ]*PRIVATE KEY"), "***REDACTED-KEY***"),
    (re.compile(r"(?i)(private_key_file\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(token\s*[=:]\s*)\S+"), r"\1***REDACTED***"),
]


def redact_ansible_text(text: str) -> str:
    out = text or ""
    for pat, repl in _REDACT:
        out = pat.sub(repl, out)
    return out


def clip_log(text: str, limit: int = MAX_LOG_CHARS) -> str:
    cleaned = redact_ansible_text(text or "")
    if len(cleaned) > limit:
        return cleaned[:limit] + "\n...[truncated]"
    return cleaned


def ansible_plays_dir() -> Path:
    return get_settings().resolved_playbooks_dir() / "ansible"


def resolve_ansible_playbook(action_id: str) -> Path:
    if not ACTION_ID_RE.match(action_id or ""):
        raise UnsafeExecutionError("拒绝非白名单 action_id / 任意命令")
    pb = get_playbook(action_id)
    if pb is None:
        raise UnsafeExecutionError(f"action_id 不在预案白名单: {action_id}")
    root = ansible_plays_dir().resolve()
    path = (root / f"{action_id}.yml").resolve()
    if path.parent != root:
        raise UnsafeExecutionError("拒绝 Playbook 目录穿越")
    if not path.is_file():
        raise UnsafeExecutionError(f"预案 {action_id} 没有对应的 Ansible 文件")
    body = path.read_text(encoding="utf-8")
    lowered = f"\n{body.lower()}"
    for tok in FORBIDDEN_ANSIBLE_TOKENS:
        if tok in lowered:
            raise UnsafeExecutionError(f"拒绝含 ad-hoc/shell 模块的 Playbook: {tok.strip()}")
    return path


def safe_ident(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if not SAFE_IDENT_RE.match(text):
        raise UnsafeExecutionError(f"非法标识符参数: {text}")
    return text


def extra_vars_from(action_id: str, asset: Any, params: dict[str, Any] | None) -> dict[str, Any]:
    pb = get_playbook(action_id)
    if pb is None:
        raise UnsafeExecutionError(f"action_id 不在预案白名单: {action_id}")
    params = params or {}
    extra = set(params) - set(pb.allowed_params)
    if extra:
        raise UnsafeExecutionError(f"参数超出预案允许字段: {sorted(extra)}")
    asset_extra = getattr(asset, "extra", None) or {}
    if isinstance(asset, dict):
        asset_extra = asset.get("extra") or {}
        hostname = asset.get("hostname") or ""
        asset_id = asset.get("id") or ""
        role = asset.get("role") or ""
    else:
        hostname = getattr(asset, "hostname", "") or ""
        asset_id = getattr(asset, "id", "") or ""
        role = getattr(asset, "role", "") or ""
    out: dict[str, Any] = {
        "asset_id": asset_id,
        "hostname": hostname,
        "action_id": action_id,
        "role": role,
    }

    def _default(name: str, fallback: Any) -> Any:
        spec = pb.allowed_params.get(name)
        if isinstance(spec, dict) and spec.get("default") is not None:
            return spec.get("default")
        return fallback

    if "batch_size" in pb.allowed_params:
        out["batch_size"] = int(params.get("batch_size") if params.get("batch_size") is not None else _default("batch_size", 1))
    if "max_age_hours" in pb.allowed_params:
        out["max_age_hours"] = int(
            params.get("max_age_hours") if params.get("max_age_hours") is not None else _default("max_age_hours", 24)
        )
    if "probe_name" in pb.allowed_params:
        out["probe_name"] = safe_ident(params.get("probe_name") or _default("probe_name", "biz-probe"), "biz-probe")
    if "force" in pb.allowed_params:
        force_val = params.get("force")
        out["force"] = bool(_default("force", False) if force_val is None else force_val)
    service = asset_extra.get("service_name") or asset_extra.get("ansible_service") or "order-app"
    out["service_name"] = safe_ident(service, "order-app")
    return out


def sanitize_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    blocked = {
        "password",
        "secret",
        "token",
        "credential",
        "private_key",
        "private_key_file",
        "ansible_ssh_private_key_file",
        "identity",
        "ssh_key",
    }
    cleaned: dict[str, Any] = {}
    for key, value in (result or {}).items():
        lowered = key.lower()
        if lowered in blocked or any(tok in lowered for tok in blocked):
            continue
        if isinstance(value, dict):
            cleaned[key] = sanitize_execution_result(value)
        elif isinstance(value, str):
            cleaned[key] = clip_log(value)
        else:
            cleaned[key] = value
    return cleaned
