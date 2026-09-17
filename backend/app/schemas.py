from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACTION_ID_RE = re.compile(r"^ACT-[A-Z0-9-]+$")
ASSET_ID_RE = re.compile(r"^ast-[a-z0-9-]+$")
FORBIDDEN_PARAM_KEYS = {
    "cmd",
    "command",
    "shell",
    "script",
    "argv",
    "executable",
    "path",
    "filepath",
    "cwd",
    "bash",
    "powershell",
}


def params_digest(asset_id: str, playbook_version: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"asset_id": asset_id, "playbook_version": playbook_version, "params": params or {}},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ToolCall(BaseModel):
    """Executor and investigation tools accept only structured identifiers."""

    asset_id: str
    action_id: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("asset_id")
    @classmethod
    def asset_shape(cls, v: str) -> str:
        if not ASSET_ID_RE.match(v):
            raise ValueError("asset_id 非法")
        return v

    @field_validator("action_id")
    @classmethod
    def action_shape(cls, v: str) -> str:
        lowered = v.lower()
        if any(tok in lowered for tok in (";", "|", "`", "$(", "rm ", "sudo", "/bin/", "bash -")):
            raise ValueError("拒绝任意命令或路径")
        if not (v.startswith("ACT-") or v.startswith("TOOL-")):
            raise ValueError("action_id 必须来自预案/工具目录")
        if v.startswith("ACT-") and not ACTION_ID_RE.match(v):
            raise ValueError("action_id 格式非法")
        return v

    @field_validator("params")
    @classmethod
    def no_dangerous_params(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key in v:
            if key.lower() in FORBIDDEN_PARAM_KEYS:
                raise ValueError(f"拒绝任意命令/路径参数: {key}")
            if isinstance(v[key], str) and any(tok in v[key] for tok in (";", "|", "`", "$(", "../")):
                raise ValueError("参数含有非法命令或路径片段")
        return v


class Diagnosis(BaseModel):
    root_cause: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    candidate_action_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = ""
    recommended_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_action_id")
    @classmethod
    def catalog_only(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not ACTION_ID_RE.match(v):
            raise ValueError("candidate_action_id 必须是预案编号，禁止命令字符串")
        return v

    @field_validator("recommended_params")
    @classmethod
    def params_safe(cls, v: dict[str, Any]) -> dict[str, Any]:
        ToolCall.no_dangerous_params(v)
        return v

    @model_validator(mode="after")
    def no_command_fields(self) -> "Diagnosis":
        dumped = self.model_dump()
        if "commands" in dumped or "shell" in dumped:
            raise ValueError("诊断不得包含命令字段")
        return self


class ZabbixWebhookIn(BaseModel):
    """兼容演示字段与 Zabbix 原生宏：eventid/host/hostid/trigger。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    event_id: str = ""
    eventid: str | None = None
    asset_id: str | None = None
    trigger_name: str = ""
    trigger: str | None = None
    triggerid: str | None = None
    severity: str = "high"
    action_type: str = "PROBLEM"
    job_version: str = "v1"
    message: str = ""
    host: str | None = None
    hostname: str | None = None
    hostid: str | None = None
    value: str = "PROBLEM"
    clock: str | int | None = None
    demo_scenario: str | None = None

    @model_validator(mode="before")
    @classmethod
    def accept_zabbix_macros(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("event_id"):
            out["event_id"] = str(out.get("eventid") or out.get("eventId") or "")
        if not out.get("trigger_name"):
            out["trigger_name"] = str(out.get("trigger") or out.get("trigger_name") or "")
        if not out.get("host") and out.get("hostname"):
            out["host"] = out.get("hostname")
        if not out.get("asset_id"):
            out["asset_id"] = None
        return out

    @model_validator(mode="after")
    def require_event_and_trigger(self) -> "ZabbixWebhookIn":
        if not (self.event_id or "").strip():
            raise ValueError("缺少 event_id / eventid")
        if not (self.trigger_name or "").strip():
            raise ValueError("缺少 trigger_name / trigger")
        if self.asset_id == "":
            self.asset_id = None
        return self

    @field_validator("asset_id")
    @classmethod
    def asset_shape(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not ASSET_ID_RE.match(v):
            raise ValueError("asset_id 非法")
        return v


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str
    status: str
    source: str
    employee_id: str
    asset_id: str
    title: str
    trigger_name: str
    severity: str
    owner: str
    risk_level: str
    policy_light: str | None
    candidate_action_id: str | None
    playbook_version: str | None
    params: dict[str, Any]
    params_digest: str | None
    diagnosis: dict[str, Any] | None
    evidence: dict[str, Any] | None
    policy_result: dict[str, Any] | None
    demo_scenario: str
    execution_count: int
    escalate_reason: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class TicketEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    actor: str
    message: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalIn(BaseModel):
    approver: str = Field(min_length=1, max_length=64)
    comment: str = ""


class PolicyResult(BaseModel):
    light: Literal["green", "yellow", "red"]
    reasons: list[str]
    action_id: str | None = None
    require_approval: bool = False
    can_execute: bool = False
    policy_version: str
