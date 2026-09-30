"""指标越限告警引擎 v2：满窗触发 / 未满窗不触发 / 幂等恢复 / 系统扩展指标 / 事件型规则 / 应用与DB规则。"""
from __future__ import annotations

from datetime import timedelta

from app.models import AnomalyEvent, Asset, SystemMetricSample, utcnow
from app.services.alert_engine import run_alert_cycle


def _policy(**over) -> dict:
    base = {
        "cpu_threshold": 90,
        "cpu_window_seconds": 300,
        "mem_threshold": 90,
        "mem_window_seconds": 300,
        "load_threshold": 1.5,
        "load_window_seconds": 300,
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


def _sample(db, asset_id: str, field: str, value: float, seconds_ago: float, ext: dict | None = None) -> None:
    db.add(
        SystemMetricSample(
            asset_id=asset_id,
            ts=utcnow() - timedelta(seconds=seconds_ago),
            **{field: value},
            ext=ext,
        )
    )
    db.commit()


def _ev(db, event_id: str) -> AnomalyEvent | None:
    return db.query(AnomalyEvent).filter_by(event_id=event_id).one_or_none()


def test_breach_full_window_triggers(auth_token, client, db):
    """子机自有策略 cpu 50%/60s：窗口内全部样本 ≥50 → 触发异常事件，级别 P1。"""
    _mk_child(db, "ch-eng", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_seconds=60)})
    _sample(db, "ch-eng", "cpu", 80, 50)
    _sample(db, "ch-eng", "cpu", 60, 40)
    _sample(db, "ch-eng", "cpu", 55, 30)
    stats = run_alert_cycle(db)
    assert stats["checked"] >= 1 and stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-eng-cpu")
    assert ev is not None and ev.status == "abnormal"
    assert ev.asset_id == "ch-eng"
    assert ev.trigger_name == "CPU 使用率"
    assert ev.severity == "P1"
    assert ev.payload["threshold"] == 50 and ev.payload["policy_source"] == "self"
    assert ev.payload["window_seconds"] == 60


def test_short_burst_does_not_trigger(auth_token, client, db):
    """窗口内混有正常样本（瞬间冲高回落）→ 不触发。"""
    _mk_child(db, "ch-burst", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_seconds=60)})
    _sample(db, "ch-burst", "cpu", 80, 50)
    _sample(db, "ch-burst", "cpu", 10, 40)
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0
    assert _ev(db, "metric-ch-burst-cpu") is None


def test_idempotent_recover_and_reopen(auth_token, client, db, monkeypatch):
    """持续越限不重复建事件；出现正常样本自动恢复；再次越限重开同一事件。"""
    import app.services.alert_engine as engine_mod

    _mk_child(db, "ch-cyc", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_seconds=60)})
    _sample(db, "ch-cyc", "cpu", 80, 50)
    _sample(db, "ch-cyc", "cpu", 70, 30)
    run_alert_cycle(db)
    ev = _ev(db, "metric-ch-cyc-cpu")
    assert ev is not None and ev.status == "abnormal"

    # 持续越限：再扫描不重复触发
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0
    assert db.query(AnomalyEvent).filter_by(event_id="metric-ch-cyc-cpu").count() == 1

    # 窗口内出现正常样本 → 自动恢复
    _sample(db, "ch-cyc", "cpu", 10, 10)
    stats = run_alert_cycle(db)
    assert stats["recovered"] >= 1
    ev = _ev(db, "metric-ch-cyc-cpu")
    assert ev.status == "recovered" and ev.recovered_at is not None

    # 时间推进后 agent 继续上报越限样本 → 重开同一事件（不新建）
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
            extra={"alert_policy": _policy(cpu_threshold=60, cpu_window_seconds=60)},
        )
    )
    db.commit()
    _mk_child(db, "ch-inh-eng", mother_id="mo-eng")
    _sample(db, "ch-inh-eng", "cpu", 70, 50)
    _sample(db, "ch-inh-eng", "cpu", 65, 30)
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-inh-eng-cpu")
    assert ev is not None and ev.payload["policy_source"] == "mother" and ev.payload["threshold"] == 60


def test_samples_below_two_do_not_trigger(auth_token, client, db):
    """窗口内样本不足 2 条（如 agent 刚上线/断连）→ 不判定触发。"""
    _mk_child(db, "ch-few", extra={"alert_policy": _policy(cpu_threshold=50, cpu_window_seconds=60)})
    _sample(db, "ch-few", "cpu", 99, 30)
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0


# ===== v2 扩展规则 =====


def test_swap_threshold_triggers(auth_token, client, db):
    """Swap 使用率满窗越限 → 触发 P2 事件；恢复后自动 recover。"""
    _mk_child(
        db,
        "ch-swap",
        extra={"alert_policy": _policy(swap_enabled=True, swap_threshold=80, swap_window_seconds=60)},
    )
    _sample(db, "ch-swap", "cpu", 10, 50, ext={"swap": 85.0})
    _sample(db, "ch-swap", "cpu", 10, 40, ext={"swap": 90.0})
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-swap-swap")
    assert ev is not None and ev.status == "abnormal" and ev.severity == "P2"
    assert ev.payload["metric"] == "swap"

    # 恢复
    _sample(db, "ch-swap", "cpu", 10, 10, ext={"swap": 20.0})
    stats = run_alert_cycle(db)
    assert stats["recovered"] >= 1
    assert _ev(db, "metric-ch-swap-swap").status == "recovered"


def test_swap_disabled_does_not_trigger(auth_token, client, db):
    """Swap 开关关闭（默认）→ 即使越限也不触发。"""
    _mk_child(db, "ch-swapoff", extra={"alert_policy": _policy()})
    _sample(db, "ch-swapoff", "cpu", 10, 50, ext={"swap": 99.0})
    _sample(db, "ch-swapoff", "cpu", 10, 40, ext={"swap": 99.0})
    stats = run_alert_cycle(db)
    assert stats["triggered"] == 0


def test_oom_event_rule(auth_token, client, db, monkeypatch):
    """OOM 事件型：窗口内出现 oom_events>0 即异常；整窗干净自动恢复。"""
    import app.services.alert_engine as engine_mod

    _mk_child(db, "ch-oom", extra={"alert_policy": _policy(oom_enabled=True, oom_level="P1", oom_window_seconds=30)})
    _sample(db, "ch-oom", "cpu", 10, 20, ext={"oom_events": 1})
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-oom-oom")
    assert ev is not None and ev.status == "abnormal" and ev.severity == "P1"

    # 时间推进 60 秒 + 干净样本 → 事件出窗，自动恢复
    future = utcnow() + timedelta(seconds=60)
    monkeypatch.setattr(engine_mod, "utcnow", lambda: future)
    db.add(SystemMetricSample(asset_id="ch-oom", ts=future - timedelta(seconds=5), cpu=10, ext={"oom_events": 0}))
    db.commit()
    run_alert_cycle(db)
    assert _ev(db, "metric-ch-oom-oom").status == "recovered"


def test_process_and_port_rules(auth_token, client, db):
    """关键进程消失 / 端口探活失败：列表非空即异常，级别可配。"""
    _mk_child(
        db,
        "ch-proc",
        extra={"alert_policy": _policy(process_enabled=True, process_level="P0", port_enabled=True, port_level="P1")},
    )
    _sample(db, "ch-proc", "cpu", 10, 50, ext={"procs_missing": ["nginx"], "ports_down": ["8080"]})
    _sample(db, "ch-proc", "cpu", 10, 40, ext={"procs_missing": [], "ports_down": []})
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 2
    ev_p = _ev(db, "metric-ch-proc-process")
    ev_port = _ev(db, "metric-ch-proc-port")
    assert ev_p is not None and ev_p.status == "abnormal" and ev_p.severity == "P0"
    assert "nginx" in ev_p.message
    assert ev_port is not None and ev_port.status == "abnormal" and ev_port.severity == "P1"


def test_app_rule_metrics_scrape(auth_token, client, db):
    """应用层规则（数据源 ext.metrics）：5xx 错误率满窗超阈值 → P1 事件。"""
    _mk_child(
        db,
        "ch-app",
        extra={
            "alert_policy": _policy(
                metrics_scrape_enabled=True,
                rules={"app_http_5xx": {"enabled": True, "threshold": 1.0, "window_seconds": 120, "level": "P1"}},
            )
        },
    )
    _sample(db, "ch-app", "cpu", 10, 100, ext={"metrics": {"app_http_5xx_rate": 2.5}})
    _sample(db, "ch-app", "cpu", 10, 80, ext={"metrics": {"app_http_5xx_rate": 3.0}})
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-app-app_http_5xx")
    assert ev is not None and ev.status == "abnormal" and ev.severity == "P1"
    assert ev.trigger_name == "HTTP 5xx 错误率"

    # 未启用规则不触发（同一资产另一规则无事件）
    assert _ev(db, "metric-ch-app-app_heap_high") is None


def test_db_rule_mysql_replica_broken(auth_token, client, db):
    """数据库层规则：MySQL 复制中断（lte 0）→ P0。"""
    _mk_child(
        db,
        "ch-db",
        extra={
            "alert_policy": _policy(
                rules={"mysql_replica_broken": {"enabled": True, "threshold": 0, "window_seconds": 60, "level": "P0"}}
            )
        },
    )
    _sample(db, "ch-db", "cpu", 10, 50, ext={"metrics": {"mysql_replica_running": 0}})
    _sample(db, "ch-db", "cpu", 10, 40, ext={"metrics": {"mysql_replica_running": 0}})
    stats = run_alert_cycle(db)
    assert stats["triggered"] >= 1
    ev = _ev(db, "metric-ch-db-mysql_replica_broken")
    assert ev is not None and ev.status == "abnormal" and ev.severity == "P0"


def test_disable_rule_recovers_event(auth_token, client, db):
    """规则被禁用 → 既有 abnormal 事件自动恢复。"""
    _mk_child(
        db,
        "ch-dis",
        extra={"alert_policy": _policy(rules={"app_http_5xx": {"enabled": True, "window_seconds": 60}})},
    )
    _sample(db, "ch-dis", "cpu", 10, 50, ext={"metrics": {"app_http_5xx_rate": 5}})
    _sample(db, "ch-dis", "cpu", 10, 40, ext={"metrics": {"app_http_5xx_rate": 5}})
    run_alert_cycle(db)
    assert _ev(db, "metric-ch-dis-app_http_5xx").status == "abnormal"

    # 禁用规则 → 自动恢复
    _mk_child(db, "ch-dis2", mother_id="", extra=None)
    asset = db.query(Asset).filter_by(id="ch-dis").one()
    asset.extra = {
        **asset.extra,
        "alert_policy": {**(asset.extra["alert_policy"]), "rules": {"app_http_5xx": {"enabled": False}}},
    }
    db.commit()
    stats = run_alert_cycle(db)
    assert stats["recovered"] >= 1
    assert _ev(db, "metric-ch-dis-app_http_5xx").status == "recovered"
