import pytest
from pydantic import ValidationError

from app.domain.safety import UnsafeExecutionError, parse_diagnosis, validate_tool_call
from app.schemas import Diagnosis, ToolCall


def test_diagnosis_accepts_catalog_action():
    d = Diagnosis(
        root_cause="Full GC",
        evidence_refs=["logs:gc"],
        candidate_action_id="ACT-ROLLING-RESTART",
        confidence=0.8,
    )
    parse_diagnosis(d.model_dump())


def test_diagnosis_rejects_invented_command():
    with pytest.raises(ValidationError):
        Diagnosis(
            root_cause="x",
            evidence_refs=["a"],
            candidate_action_id="rm -rf /",
            confidence=0.9,
        )


def test_diagnosis_rejects_shell_like_action():
    with pytest.raises(ValidationError):
        Diagnosis(
            root_cause="x",
            evidence_refs=["a"],
            candidate_action_id="ACT-ROLLING-RESTART; reboot",
            confidence=0.9,
        )


def test_unknown_catalog_id_rejected():
    with pytest.raises(UnsafeExecutionError):
        parse_diagnosis(
            {
                "root_cause": "x",
                "evidence_refs": ["a"],
                "candidate_action_id": "ACT-INVENTED-SHELL",
                "confidence": 0.5,
            }
        )


def test_tool_call_rejects_arbitrary_shell():
    with pytest.raises(ValidationError):
        ToolCall(asset_id="ast-order-app-01", action_id="ACT-ROLLING-RESTART", params={"cmd": "reboot"})


def test_tool_call_rejects_path_param():
    with pytest.raises(ValidationError):
        ToolCall(asset_id="ast-order-app-01", action_id="ACT-ROLLING-RESTART", params={"path": "/etc/passwd"})


def test_executor_rejects_cross_tenant():
    with pytest.raises(UnsafeExecutionError):
        validate_tool_call(
            asset_id="ast-order-app-01",
            action_id="ACT-ROLLING-RESTART",
            params={"batch_size": 1},
            tenant_id="tenant-default",
            asset_tenant_id="tenant-other",
        )


def test_executor_rejects_unknown_playbook():
    with pytest.raises(UnsafeExecutionError):
        validate_tool_call(
            asset_id="ast-order-app-01",
            action_id="ACT-NOT-REAL",
            params={},
            tenant_id="tenant-default",
            asset_tenant_id="tenant-default",
        )
