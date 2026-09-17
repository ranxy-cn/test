from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.domain.catalog import get_playbook
from app.schemas import Diagnosis, ToolCall


class UnsafeExecutionError(ValueError):
    pass


class CrossTenantError(UnsafeExecutionError):
    pass


def validate_diagnosis_against_catalog(diagnosis: Diagnosis) -> Diagnosis:
    """AI 只能推荐目录中的预案编号。"""
    if diagnosis.candidate_action_id is None:
        return diagnosis
    pb = get_playbook(diagnosis.candidate_action_id)
    if pb is None:
        raise UnsafeExecutionError(
            f"candidate_action_id 不在预案白名单: {diagnosis.candidate_action_id}"
        )
    extra = set(diagnosis.recommended_params) - set(pb.allowed_params)
    if extra:
        raise UnsafeExecutionError(f"诊断参数超出预案允许字段: {sorted(extra)}")
    return diagnosis


def parse_diagnosis(data: dict[str, Any]) -> Diagnosis:
    try:
        diag = Diagnosis.model_validate(data)
    except ValidationError as exc:
        raise UnsafeExecutionError(f"诊断结构非法: {exc}") from exc
    return validate_diagnosis_against_catalog(diag)


def validate_tool_call(
    *,
    asset_id: str,
    action_id: str,
    params: dict[str, Any] | None,
    tenant_id: str,
    asset_tenant_id: str,
    allow_investigation_tools: bool = False,
) -> ToolCall:
    try:
        call = ToolCall(asset_id=asset_id, action_id=action_id, params=params or {})
    except ValidationError as exc:
        raise UnsafeExecutionError(str(exc)) from exc

    if tenant_id != asset_tenant_id:
        raise CrossTenantError("拒绝跨租户目标")

    if call.action_id.startswith("TOOL-"):
        if not allow_investigation_tools:
            raise UnsafeExecutionError("执行器不接受排查工具编号")
        return call

    pb = get_playbook(call.action_id)
    if pb is None:
        raise UnsafeExecutionError(f"action_id 不在预案白名单: {call.action_id}")
    extra = set(call.params) - set(pb.allowed_params)
    if extra:
        raise UnsafeExecutionError(f"参数超出预案允许字段: {sorted(extra)}")
    return call


def never_give_secrets_to_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip any credential-like keys before model context is built."""
    blocked = {"password", "secret", "token", "credential", "lease", "private_key", "private_key_file", "identity", "ssh_key"}
    cleaned = {}
    for k, v in payload.items():
        if k.lower() in blocked or any(b in k.lower() for b in blocked):
            continue
        if isinstance(v, dict):
            cleaned[k] = never_give_secrets_to_llm(v)
        else:
            cleaned[k] = v
    return cleaned


def cooldown_violated(last_restart_at: datetime | None, now: datetime | None = None) -> bool:
    if last_restart_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    last = last_restart_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    seconds = (now - last).total_seconds()
    return seconds < get_settings().restart_cooldown_seconds
