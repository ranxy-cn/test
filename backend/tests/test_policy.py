from datetime import datetime, timedelta, timezone

from app.domain.policy import AssetContext, evaluate_policy
from app.schemas import Diagnosis


def _diag(action: str | None, confidence: float = 0.8) -> Diagnosis:
    return Diagnosis(
        root_cause="test",
        evidence_refs=["metrics:cpu"],
        candidate_action_id=action,
        confidence=confidence,
        summary="t",
    )


def _asset(**kwargs) -> AssetContext:
    data = dict(
        asset_id="ast-order-app-01",
        tenant_id="tenant-default",
        reachable=True,
        db_ok=True,
        last_restart_at=None,
        in_maintenance=False,
        role="app",
    )
    data.update(kwargs)
    return AssetContext(**data)


def test_green_rolling_restart():
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset())
    assert result.light == "green"
    assert result.can_execute is True


def test_yellow_db_failover():
    result = evaluate_policy(_diag("ACT-DB-FAILOVER"), _asset(role="mysql", db_ok=False))
    assert result.light == "yellow"
    assert result.require_approval is True


def test_red_unknown_action():
    result = evaluate_policy(_diag(None), _asset())
    assert result.light == "red"
    assert result.can_execute is False


def test_red_unreachable():
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset(reachable=False))
    assert result.light == "red"
    assert "不可达" in "".join(result.reasons)


def test_red_db_not_ok():
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset(db_ok=False))
    assert result.light == "red"


def test_red_recent_restart():
    last = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset(last_restart_at=last))
    assert result.light == "red"
    assert "循环" in "".join(result.reasons)


def test_red_maintenance():
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset(in_maintenance=True))
    assert result.light == "red"


def test_red_action_fail_cooldown():
    last = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = evaluate_policy(_diag("ACT-ROLLING-RESTART"), _asset(action_failed_at=last))
    assert result.light == "red"
    assert "冷却" in "".join(result.reasons)


def test_green_clean_tmplog():
    result = evaluate_policy(_diag("ACT-CLEAN-TMPLOG"), _asset(db_ok=False))
    assert result.light == "green"


def test_green_restart_probe():
    result = evaluate_policy(_diag("ACT-RESTART-PROBE"), _asset(db_ok=False))
    assert result.light == "green"
    assert result.can_execute is True

