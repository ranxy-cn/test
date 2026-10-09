"""告警恢复任务测试：任务生成/幂等升级/脚本自动执行/恢复关闭/API 鉴权。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import (
    AnomalyEvent,
    Asset,
    RecoveryScript,
    RecoveryTask,
    utcnow,
)
from app.services import recovery as recovery_svc


def _mk_anomaly(db, event_id="wb-rec-001", severity="P1", rule_id="cpu", asset_id=None):
    ev = AnomalyEvent(
        event_id=event_id,
        hostname="srv-rec",
        ip="10.1.1.7",
        trigger_name="CPU 使用率",
        severity=severity,
        message="CPU 过高",
        status="abnormal",
        asset_id=asset_id,
        payload={"rule_id": rule_id, "metric": rule_id},
    )
    db.add(ev)
    db.commit()
    return ev


def test_ensure_creates_task_with_severity_priority(db):
    """P 级映射基础优先级；无命中脚本时生成纯人工任务。"""
    ev = _mk_anomaly(db, severity="P1")
    task = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()
    assert task is not None and task.status == "open"
    assert task.priority == recovery_svc.SEVERITY_BASE["P1"]
    assert task.rule_key == "cpu"
    assert task.script_id is None  # 无脚本 → 纯人工


def test_ensure_idempotent_and_escalates_priority(db):
    """同 event_id 已有未结任务：不重复创建，优先级叠加（封顶 99）。"""
    ev = _mk_anomaly(db, severity="P2")
    t1 = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()
    t2 = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()
    assert t2.id == t1.id
    assert t2.priority == recovery_svc.SEVERITY_BASE["P2"] + recovery_svc.ESCALATE_STEP
    assert db.scalar(select(RecoveryTask).where(RecoveryTask.event_id == ev.event_id)) is not None


def test_low_risk_script_auto_executes(db, monkeypatch):
    """低风险脚本命中：任务绑定脚本 + 后台自动执行入口被触发。"""
    calls = []
    monkeypatch.setattr(recovery_svc, "_auto_execute", lambda tid: calls.append(tid))
    db.add(RecoveryScript(name="降载", rule_key="cpu", risk_level="low", command="echo ok"))
    db.commit()
    ev = _mk_anomaly(db, rule_id="cpu")
    task = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()
    assert task.script_id is not None and task.script_name == "降载"
    assert calls == [task.id]  # 自动执行入口收到任务 id（测试中 mock 为同步记录）


def test_execute_task_success_closes(db, monkeypatch):
    """脚本执行成功：留痕输出、任务 done、resolved_at 写入。"""
    asset = db.scalar(select(Asset).limit(1))
    asset.extra = {**(asset.extra or {}), "provision": {"ip": "10.9.9.9", "username": "root", "password": "x"}}
    db.commit()
    script = RecoveryScript(name="重启服务", rule_key="cpu", risk_level="high", command="systemctl restart app")
    db.add(script)
    db.commit()
    ev = _mk_anomaly(db, asset_id=asset.id)
    task = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()

    monkeypatch.setattr(recovery_svc, "_ssh_exec", lambda target, cmd, timeout: (True, "restarted"))
    result = recovery_svc.execute_task(task.id, operator="alice")
    db.expire_all()
    t = db.get(RecoveryTask, task.id)
    assert result["ok"] is True
    assert t.status == "done" and t.execute_ok is True
    assert t.execute_output == "restarted"
    assert t.executed_by == "alice" and t.resolved_at is not None


def test_resolve_closes_open_tasks(db):
    """告警恢复：未结任务自动关闭（done + 原因），已结不受影响。"""
    ev = _mk_anomaly(db)
    task = recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()
    closed = recovery_svc.resolve_for_anomaly(db, ev.event_id)
    db.commit()
    assert closed == 1
    db.expire_all()
    t = db.get(RecoveryTask, task.id)
    assert t.status == "done" and t.resolved_at is not None
    assert "恢复" in t.resolve_reason
    # 幂等：再 resolve 无任务可关
    assert recovery_svc.resolve_for_anomaly(db, ev.event_id) == 0


def test_recovery_api_permissions_and_crud(client, db):
    """API：任务列表 / 脚本 CRUD（admin 全权限）。"""
    # 建任务 → 列表可见（默认 status=open）
    ev = _mk_anomaly(db, event_id="wb-api-001")
    recovery_svc.ensure_for_anomaly(db, ev)
    db.commit()

    r = client.get("/api/v1/recovery/tasks")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and body["items"][0]["event_id"] == "wb-api-001"

    # 纯人工任务执行 → 明确报错（无脚本）
    tid = body["items"][0]["id"]
    r = client.post(f"/api/v1/recovery/tasks/{tid}/execute")
    assert r.status_code == 200 and r.json()["ok"] is False

    # 脚本 CRUD
    r = client.post("/api/v1/recovery/scripts", json={
        "name": "清理磁盘", "description": "清理大日志", "rule_key": "",
        "risk_level": "high", "command": "rm -f /tmp/big.log", "timeout_seconds": 30, "enabled": True,
    })
    assert r.status_code == 200
    sid = r.json()["id"]
    r = client.get("/api/v1/recovery/scripts")
    assert any(s["id"] == sid for s in r.json()["items"])
    r = client.put(f"/api/v1/recovery/scripts/{sid}", json={
        "name": "清理磁盘v2", "description": "", "rule_key": "await_ms",
        "risk_level": "low", "command": "echo hi", "timeout_seconds": 15, "enabled": False,
    })
    assert r.status_code == 200
    r = client.delete(f"/api/v1/recovery/scripts/{sid}")
    assert r.status_code == 200

    # 非法风险等级被校验拦截
    r = client.post("/api/v1/recovery/scripts", json={
        "name": "x", "command": "echo", "risk_level": "medium",
    })
    assert r.status_code == 422


def test_alert_engine_resolves_task_on_recovery(db):
    """端到端：告警触发建任务 → 指标回落后 run_alert_cycle 自动关闭任务。"""
    from app.models import SystemMetricSample
    from app.services.alert_engine import run_alert_cycle

    asset = db.scalar(select(Asset).limit(1))
    now = utcnow()
    for i in range(6):
        db.add(SystemMetricSample(asset_id=asset.id, ts=now - timedelta(seconds=60 * (5 - i)),
                                  cpu=96.0, mem=80.0, disk=50.0, load1=2.0, source="agent"))
    db.commit()
    stats = run_alert_cycle(db)
    assert stats.get("recovery_ensured", 0) >= 1
    ev = db.scalar(select(AnomalyEvent).where(AnomalyEvent.event_id.like(f"metric-{asset.id}-%")))
    assert ev is not None
    task = db.scalar(select(RecoveryTask).where(RecoveryTask.event_id == ev.event_id))
    assert task is not None and task.status == "open"

    # 指标恢复 → 引擎自动恢复告警 + 关闭任务
    for i in range(6):
        db.add(SystemMetricSample(asset_id=asset.id, ts=now + timedelta(seconds=30 * (i + 1)),
                                  cpu=10.0, mem=20.0, disk=50.0, load1=0.5, source="agent"))
    db.commit()
    stats2 = run_alert_cycle(db)
    assert stats2.get("recovery_resolved_count", 0) >= 1
    db.expire_all()
    t = db.get(RecoveryTask, task.id)
    assert t.status == "done" and t.resolved_at is not None


def test_alert_engine_payload_carries_attribution(db):
    """b3 归因链路：样本 ext.procs/net 速率 → 告警 payload.top_processes/net_rate。"""
    from app.models import SystemMetricSample
    from app.services.alert_engine import run_alert_cycle

    asset = db.scalar(select(Asset).limit(1))
    now = utcnow()
    procs = [{"pid": 32459, "comm": "stress-ng", "cpu": 99.0, "mem": 1.2}]
    for i in range(6):
        db.add(SystemMetricSample(
            asset_id=asset.id, ts=now - timedelta(seconds=60 * (5 - i)),
            cpu=96.0, mem=80.0, disk=50.0, load1=2.0,
            net_rx_bps=125000000.0, net_tx_bps=1000.0,
            ext={"procs": procs, "bw_rx_pct": 95.0}, source="agent",
        ))
    db.commit()
    stats = run_alert_cycle(db)
    assert stats.get("triggered", 0) >= 1
    ev = db.scalar(select(AnomalyEvent).where(AnomalyEvent.event_id == f"metric-{asset.id}-cpu"))
    assert ev is not None
    payload = ev.payload or {}
    assert payload.get("top_processes") == procs
    rate = payload.get("net_rate") or {}
    assert rate.get("rx_mbps") == 125.0 and rate.get("bw_rx_pct") == 95.0
