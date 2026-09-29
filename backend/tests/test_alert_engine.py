"""指标越限告警引擎：满窗触发 / 未满窗不触发 / 幂等 / 自动恢复 / 继承母机策略。"""
from __future__ import annotations

from datetime import timedelta

from app.models import AnomalyEvent, Asset, SystemMetricSample, utcnow
from app.services.alert_engine import run_alert_cycle


def _policy(**over) -> dict:
    base = {
        "cpu_threshold": 90,
        "cpu_window_minutes": 5,
        "mem_threshold": 90,
        "mem_window_minutes": 5,
        "load_threshold": 1.5,
        "load_window_minutes": 5,
    }
    base.update(over)
    return base


def _mk_child(db, asset_id: str, mother_id: str = "", extra: dict | None = None) -> None:
    db.add(
        Asset(
            id=asset_id,
            hostname=asset_id,
            app="演示App",
            role="app",
            owner="",
            kind="child",
            mother_id=mother_id,
            tenant_id="tenant-default",
            extra=dict(extra or {}),
        )
    )
    db.commit()


def _sample(db, asset_id: str, field: str, value: float, minutes_ago: float) -> None:
    db.add(SystemMetricSample(asset_id=asset_id, ts=utcnow() - timedelta(minutes=minutes_ago), **{field: value}))
    db.commit()


def _ev(db, event_id: str) -> AnomalyEvent | None:
    return db.query(AnomalyEvent).filter_by(event_id=event_id).one_or_none()


def test_breach_full_window_triggers(auth_token, client, db):
    """子机自有策略 cpu 50%/1min：窗口内全部样本 ≥50 → 触发异常事件。"""
    _mk_child(db, "ch-eng", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_minutes=1)})
    _sample(db, "ch-eng", "cpu", 80, 0.5)
    _sample(db, "ch-eng", "cpu", 60, 0.4)
    _sample(db, "ch-eng", "cpu", 55, 0.3)
    stats = run_alert_cycle(db)
    assert stats["checked"] >= 1 and stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-eng-cpu")
    assert ev is not None and ev.status == "abnormal"
    assert ev.asset_id == "ch-eng"
    assert "50" in ev.trigger_name and "1 分钟" in ev.trigger_name
    assert ev.payload["threshold"] == 50 and ev.payload["policy_source"] == "self"


def test_short_burst_does_not_trigger(auth_token, client, db):
    """窗口内混有正常样本（瞬间冲高回落）→ 不触发。"""
    _mk_child(db, "ch-burst", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_minutes=1)})
    _sample(db, "ch-burst", "cpu", 80, 0.5)
    _sample(db, "ch-burst", "cpu", 10, 0.4)
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0
    assert _ev(db, "metric-ch-burst-cpu") is None


def test_idempotent_recover_and_reopen(auth_token, client, db, monkeypatch):
    """持续越限不重复建事件；出现正常样本自动恢复；再次越限重开同一事件。"""
    import app.services.alert_engine as engine_mod

    _mk_child(db, "ch-cyc", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_minutes=1)})
    _sample(db, "ch-cyc", "cpu", 80, 0.5)
    _sample(db, "ch-cyc", "cpu", 70, 0.3)
    run_alert_cycle(db)
    ev = _ev(db, "metric-ch-cyc-cpu")
    assert ev is not None and ev.status == "abnormal"

    # 持续越限：再扫描不重复触发
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0
    assert db.query(AnomalyEvent).filter_by(event_id="metric-ch-cyc-cpu").count() == 1

    # 窗口内出现正常样本 → 自动恢复
    _sample(db, "ch-cyc", "cpu", 10, 0.1)
    stats = run_alert_cycle(db)
    assert stats["recovered"] >= 1
    ev = _ev(db, "metric-ch-cyc-cpu")
    assert ev.status == "recovered" and ev.recovered_at is not None

    # 时间推进 2 分钟后 agent 继续上报越限样本 → 重开同一事件（不新建）
    future = utcnow() + timedelta(minutes=2)
    monkeypatch.setattr(engine_mod, "utcnow", lambda: future)
    db.add(SystemMetricSample(asset_id="ch-cyc", ts=future - timedelta(seconds=5), cpu=90))
    db.add(SystemMetricSample(asset_id="ch-cyc", ts=future - timedelta(seconds=3), cpu=88))
    db.commit()
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    assert db.query(AnomalyEvent).filter_by(event_id="metric-ch-cyc-cpu").count() == 1
    assert _ev(db, "metric-ch-cyc-cpu").status == "abnormal"


def test_child_without_own_policy_uses_mother(auth_token, client, db):
    """子机无自有策略 → 继承母机策略判定。"""
    db.add(
        Asset(
            id="mo-eng",
            hostname="mo-eng",
            app="运维平台",
            role="app",
            owner="",
            kind="mother",
            tenant_id="tenant-default",
            extra={"alert_policy": _policy(cpu_threshold=60, cpu_window_minutes=1)},
        )
    )
    db.commit()
    _mk_child(db, "ch-inh-eng", mother_id="mo-eng")
    _sample(db, "ch-inh-eng", "cpu", 70, 0.5)
    _sample(db, "ch-inh-eng", "cpu", 65, 0.3)
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-inh-eng-cpu")
    assert ev is not None and ev.payload["policy_source"] == "mother" and ev.payload["threshold"] == 60


def test_samples_below_two_do_not_trigger(auth_token, client, db):
    """窗口内样本不足 2 条（如 agent 刚上线/断连）→ 不判定触发。"""
    _mk_child(db, "ch-few", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_minutes=1)})
    _sample(db, "ch-few", "cpu", 99, 0.3)
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0
