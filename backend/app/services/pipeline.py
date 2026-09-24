from __future__ import annotations

import uuid
from datetime import timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agent.graph import run_investigation
from app.config import get_settings
from app.domain.catalog import get_playbook
from app.domain.policy import AssetContext, evaluate_policy
from app.domain.safety import UnsafeExecutionError, parse_diagnosis, validate_tool_call
from app.domain.state_machine import transition
from app.executor.verifier import BusinessProbeVerifier
from app.integrations import get_playbook_runner
from app.integrations.zabbix.mapping import mapping_from_asset
from app.models import AlertEvent, Approval, Asset, MaintenanceWindow, Ticket, TicketSource, TicketStatus, utcnow
from app.schemas import Diagnosis, params_digest
from app.services.audit import add_audit, add_event
from app.services.cooldowns import last_action_failure, record_action_failure
from app.services.locks import acquire_asset_lock, heartbeat_lock, record_lock_conflict, release_asset_lock
from app.services.notify import notify_ticket
from app.services.tickets import next_ticket_number
from sqlalchemy import select


def _set_status(ticket: Ticket, target: TicketStatus) -> None:
    ticket.status = transition(ticket.status, target).value
    ticket.updated_at = utcnow()


def in_maintenance(db: Session, asset_id: str):
    now = utcnow()
    return db.scalar(
        select(MaintenanceWindow).where(
            MaintenanceWindow.asset_id == asset_id,
            MaintenanceWindow.starts_at <= now,
            MaintenanceWindow.ends_at >= now,
        )
    )


def asset_context(db: Session, asset: Asset, action_id: str | None = None) -> AssetContext:
    mw = in_maintenance(db, asset.id)
    return AssetContext(
        asset_id=asset.id,
        tenant_id=asset.tenant_id,
        reachable=asset.reachable,
        db_ok=asset.db_ok,
        last_restart_at=asset.last_restart_at,
        in_maintenance=mw is not None,
        role=asset.role,
        action_failed_at=last_action_failure(db, asset.id, action_id),
    )


def dispatch_investigation(ticket_id: int) -> None:
    if get_settings().use_celery:
        from app.workers.tasks import investigate_ticket

        investigate_ticket.delay(ticket_id)
        return
    run_pipeline(ticket_id)


def dispatch_execution(ticket_id: int) -> None:
    if get_settings().use_celery:
        from app.workers.tasks import execute_ticket

        execute_ticket.delay(ticket_id)
        return
    run_execution(ticket_id)


class ManualActionRejected(Exception):
    """策略引擎红灯，拒绝人工发起的动作。"""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("；".join(reasons))


def create_manual_ticket(
    db: Session,
    *,
    asset: Asset,
    action_id: str,
    params: dict[str, Any] | None = None,
    reason: str = "",
    actor: str = "",
) -> Ticket:
    """人工一键发起白名单预案：与告警链路共用同一策略引擎 / 审批 / 执行管线。

    绿灯 → 直接排队执行；黄灯 → 挂起等审批；红灯 → 升级并抛 ManualActionRejected。
    """
    settings = get_settings()
    pb = get_playbook(action_id)
    if pb is None:
        raise ValueError(f"action_id 不在预案白名单: {action_id}")

    uid = uuid.uuid4().hex[:12]
    ticket = Ticket(
        number=next_ticket_number(db),
        idempotency_key=f"manual|{asset.id}|{action_id}|{uid}",
        source=TicketSource.manual.value,
        status=TicketStatus.pending_analysis.value,
        employee_id=settings.employee_id,
        asset_id=asset.id,
        tenant_id=asset.tenant_id,
        title=f"人工发起：{pb.name}",
        trigger_name="",
        severity="info",
        action_type="MANUAL",
        job_version=f"manual-{pb.version}",
        event_id=f"manual-{uid}",
        owner=asset.owner,
        demo_scenario="green",
    )
    db.add(ticket)
    db.flush()

    diagnosis = Diagnosis(
        root_cause="人工发起运维动作",
        evidence_refs=[f"manual-request:{uid}"],
        candidate_action_id=action_id,
        confidence=1.0,
        summary=reason or f"人工发起 {pb.name}",
        recommended_params=params or {},
    )
    ctx = asset_context(db, asset, action_id)
    policy = evaluate_policy(diagnosis, ctx)
    ticket.policy_result = policy.model_dump()
    ticket.policy_light = policy.light
    ticket.candidate_action_id = policy.action_id
    ticket.playbook_version = pb.version
    ticket.params = params or {}
    ticket.params_digest = params_digest(asset.id, pb.version, params or {})
    ticket.risk_level = pb.risk
    add_event(
        db,
        ticket_id=ticket.id,
        kind="manual_requested",
        actor=actor or "manual",
        message=f"人工发起 {pb.id}@{pb.version}，策略{policy.light}",
        payload=policy.model_dump(),
    )
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="manual_request",
        actor=actor or "manual",
        result={"action_id": action_id, "light": policy.light, "reasons": policy.reasons},
        playbook_version=pb.version,
        params_digest=ticket.params_digest,
        policy_version=get_settings().policy_version,
    )

    if policy.light == "red":
        _escalate(db, ticket, "；".join(policy.reasons))
        db.flush()
        raise ManualActionRejected(policy.reasons)
    if policy.light == "yellow":
        _request_approval(db, ticket, pb.id)
        notify_ticket(db, ticket, "pending_approval", f"{ticket.number} 人工发起 {pb.id}，等待审批")
        db.flush()
    else:
        _set_status(ticket, TicketStatus.pending_execution)
        add_event(db, ticket_id=ticket.id, kind="auto_execute_authorized", message="绿灯：进入自动执行")
        db.flush()
    return ticket


def run_pipeline(ticket_id: int) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        asset = db.get(Asset, ticket.asset_id)
        if asset is None:
            _escalate(db, ticket, "资产不存在")
            db.commit()
            return

        add_event(
            db,
            ticket_id=ticket.id,
            kind="owner_notified",
            message=f"已通知负责人 {asset.owner}：发现异常，正在自动排查",
        )
        alert = {
            "event_id": ticket.event_id,
            "asset_id": ticket.asset_id,
            "trigger_name": ticket.trigger_name,
            "message": ticket.title,
            "demo_scenario": ticket.demo_scenario,
        }
        alert_row = db.scalar(select(AlertEvent).where(AlertEvent.ticket_id == ticket.id))
        webhook_payload = (alert_row.payload if alert_row else None) or {}
        if webhook_payload.get("host"):
            alert["host"] = webhook_payload.get("host")
        if webhook_payload.get("hostid"):
            alert["hostid"] = webhook_payload.get("hostid")
        state = run_investigation(
            {
                "ticket_id": ticket.id,
                "asset_id": ticket.asset_id,
                "tenant_id": ticket.tenant_id,
                "asset_tenant_id": asset.tenant_id,
                "db_ok": asset.db_ok,
                "alert": alert,
                "host_hint": mapping_from_asset(asset, webhook_payload),
                "event_id": ticket.event_id,
            }
        )
        evidence = state.get("evidence") or {}
        diagnosis = parse_diagnosis(state["diagnosis"])
        model_version = state.get("model_version")
        ticket.evidence = evidence
        ticket.diagnosis = diagnosis.model_dump()
        add_event(
            db,
            ticket_id=ticket.id,
            kind="evidence_gathered",
            message="并行采集指标/脱敏日志/发版/依赖/手册命中完成",
            payload={"refs": _evidence_refs(evidence)},
        )
        add_event(
            db,
            ticket_id=ticket.id,
            kind="diagnosis_completed",
            message=diagnosis.summary or diagnosis.root_cause,
            payload=diagnosis.model_dump(),
        )
        add_audit(
            db,
            ticket_id=ticket.id,
            event_type="diagnosis",
            result=diagnosis.model_dump(),
            evidence_refs=diagnosis.evidence_refs,
            model_version=model_version,
            policy_version=get_settings().policy_version,
        )

        ctx = asset_context(db, asset, diagnosis.candidate_action_id)
        policy = evaluate_policy(diagnosis, ctx)
        ticket.policy_result = policy.model_dump()
        ticket.policy_light = policy.light
        ticket.candidate_action_id = policy.action_id
        pb = get_playbook(policy.action_id) if policy.action_id else None
        if pb:
            ticket.playbook_version = pb.version
            ticket.params = diagnosis.recommended_params or {}
            ticket.params_digest = params_digest(ticket.asset_id, pb.version, ticket.params)
            ticket.risk_level = pb.risk
        lights = {"green": "绿灯", "yellow": "黄灯", "red": "红灯"}
        add_event(
            db,
            ticket_id=ticket.id,
            kind="policy_evaluated",
            message=f"策略{lights[policy.light]}：" + "；".join(policy.reasons),
            payload=policy.model_dump(),
        )
        add_audit(
            db,
            ticket_id=ticket.id,
            event_type="policy",
            result=policy.model_dump(),
            policy_version=policy.policy_version,
            playbook_version=ticket.playbook_version,
            params_digest=ticket.params_digest,
            evidence_refs=diagnosis.evidence_refs,
        )

        if policy.light == "red":
            _escalate(db, ticket, "；".join(policy.reasons))
        elif policy.light == "yellow":
            _request_approval(db, ticket, pb.id if pb else (policy.action_id or ""))
            notify_ticket(db, ticket, "pending_approval", f"{ticket.number} 等待审批 {ticket.candidate_action_id}")
        else:
            _set_status(ticket, TicketStatus.pending_execution)
            add_event(db, ticket_id=ticket.id, kind="auto_execute_authorized", message="绿灯：进入自动执行")
            db.commit()
            run_execution(ticket.id, db=db)
            return
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ticket = db.get(Ticket, ticket_id)
        if ticket and ticket.status == TicketStatus.pending_analysis.value:
            _escalate(db, ticket, f"排查失败: {exc}")
            db.commit()
        else:
            raise
    finally:
        db.close()


def run_execution(ticket_id: int, db: Session | None = None) -> None:
    close = False
    acquired = False
    asset_id_held = None
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
        close = True
    try:
        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return
        if ticket.status != TicketStatus.pending_execution.value:
            return
        if ticket.execution_count >= 1:
            _escalate(db, ticket, "已执行过一次，验证失败后禁止循环重启")
            db.commit()
            return

        asset = db.get(Asset, ticket.asset_id)
        pb = get_playbook(ticket.candidate_action_id or "")
        if asset is None or pb is None:
            _escalate(db, ticket, "执行前校验失败：资产或预案缺失")
            db.commit()
            return

        ok, held = acquire_asset_lock(db, asset_id=ticket.asset_id, ticket_id=ticket.id)
        if not ok:
            record_lock_conflict(db, ticket.id, ticket.asset_id, held.ticket_id if held else 0)
            notify_ticket(db, ticket, "lock_queued", f"{ticket.number} 等待资源锁 {ticket.asset_id}")
            db.commit()
            return
        acquired = True
        asset_id_held = ticket.asset_id

        try:
            call = validate_tool_call(
                asset_id=ticket.asset_id,
                action_id=ticket.candidate_action_id or "",
                params=ticket.params or {},
                tenant_id=ticket.tenant_id,
                asset_tenant_id=asset.tenant_id,
            )
        except UnsafeExecutionError as exc:
            _escalate(db, ticket, f"拒绝执行：{exc}")
            db.commit()
            return

        _set_status(ticket, TicketStatus.executing)
        ticket.execution_count += 1
        add_event(db, ticket_id=ticket.id, kind="execution_started", message=f"按剧本 {pb.id}@{pb.version} 执行")
        db.flush()

        runner = get_playbook_runner()

        def on_step(step: dict[str, Any]) -> None:
            heartbeat_lock(db, ticket.asset_id, ticket.id)
            add_event(
                db,
                ticket_id=ticket.id,
                kind="playbook_step",
                message=f"{step.get('name')} → {step.get('status')}",
                payload=step,
            )

        result = runner.run(
            call=call,
            playbook=pb,
            tenant_id=ticket.tenant_id,
            asset_tenant_id=asset.tenant_id,
            on_step=on_step,
            asset=asset,
        )
        from app.domain.safety import never_give_secrets_to_llm
        from app.integrations.ansible.safety import sanitize_execution_result

        safe_result = sanitize_execution_result(never_give_secrets_to_llm(result))
        evidence = dict(ticket.evidence or {})
        evidence["execution"] = {
            "ok": safe_result.get("ok"),
            "runner": safe_result.get("runner") or runner.name,
            "playbook": safe_result.get("playbook_file") or pb.id,
            "playbook_id": pb.id,
            "rc": safe_result.get("rc"),
            "stdout_excerpt": safe_result.get("stdout_excerpt"),
            "mode": safe_result.get("mode"),
            "inventory_source": safe_result.get("inventory_source"),
            "steps": safe_result.get("steps"),
        }
        ticket.evidence = evidence
        add_audit(
            db,
            ticket_id=ticket.id,
            event_type="execution",
            result={
                "ok": safe_result.get("ok"),
                "steps": safe_result.get("steps"),
                "lease_id": safe_result.get("lease_id"),
                "playbook": safe_result.get("playbook_file") or pb.id,
                "rc": safe_result.get("rc"),
                "stdout_excerpt": safe_result.get("stdout_excerpt"),
                "runner": safe_result.get("runner") or runner.name,
                "mode": safe_result.get("mode"),
            },
            playbook_version=pb.version,
            params_digest=ticket.params_digest,
            policy_version=get_settings().policy_version,
        )
        if not result["ok"]:
            record_action_failure(db, ticket.asset_id, ticket.candidate_action_id or "", "Playbook 执行失败")
            _escalate(db, ticket, "Playbook 执行失败，停止并升级")
            db.commit()
            return

        if pb.restart_cooldown:
            asset.last_restart_at = utcnow()

        # 执行成功 → 回写 CMDB，形成“部署 → 纳管”闭环
        if result["ok"]:
            asset.reachable = True
            if pb.id == "ACT-DEPLOY-ZABBIX-AGENT":
                extra = dict(asset.extra or {})
                extra["zabbix_agent"] = {
                    "deployed_at": utcnow().isoformat(),
                    "zabbix_server": (ticket.params or {}).get("zabbix_server") or "",
                    "ticket": ticket.number,
                }
                asset.extra = extra
                asset.zabbix_host = asset.hostname
                add_event(
                    db,
                    ticket_id=ticket.id,
                    kind="asset_synced",
                    message="已回写 CMDB：reachable=true，zabbix_host 已绑定",
                )

        _set_status(ticket, TicketStatus.verifying)
        add_event(db, ticket_id=ticket.id, kind="verification_started", message="开始业务探测与观察期")
        heartbeat_lock(db, ticket.asset_id, ticket.id)
        db.flush()

        verifier = BusinessProbeVerifier()
        verify = verifier.verify(asset_id=ticket.asset_id, scenario=ticket.demo_scenario)
        add_event(
            db,
            ticket_id=ticket.id,
            kind="probe_result",
            message="探测通过" if verify["ok"] else verify.get("reason") or "探测失败",
            payload=verify,
        )
        add_audit(
            db,
            ticket_id=ticket.id,
            event_type="verification",
            result=verify,
            playbook_version=pb.version,
            params_digest=ticket.params_digest,
        )
        if not verify["ok"]:
            record_action_failure(
                db, ticket.asset_id, ticket.candidate_action_id or "", verify.get("reason") or "验证失败"
            )
            _escalate(db, ticket, verify.get("reason") or "验证失败，禁止循环重启")
            db.commit()
            return

        _set_status(ticket, TicketStatus.recovered)
        ticket.closed_at = utcnow()
        add_event(
            db,
            ticket_id=ticket.id,
            kind="recovered",
            message=f"已自动恢复。根因：{(ticket.diagnosis or {}).get('root_cause', '')}。建议尽快修复漏洞。",
        )
        add_audit(
            db,
            ticket_id=ticket.id,
            event_type="recovered",
            result={"status": "recovered"},
            playbook_version=pb.version,
            params_digest=ticket.params_digest,
            evidence_refs=(ticket.diagnosis or {}).get("evidence_refs") or [],
        )
        notify_ticket(db, ticket, "recovered", f"{ticket.number} 已自动恢复")
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        ticket = db.get(Ticket, ticket_id)
        if ticket and ticket.status not in {TicketStatus.recovered.value, TicketStatus.escalated.value}:
            _escalate(db, ticket, f"执行异常: {exc}")
            db.commit()
        else:
            raise
    finally:
        if acquired and asset_id_held:
            try:
                release_asset_lock(db, asset_id_held, ticket_id)
                db.commit()
            except Exception:
                db.rollback()
        if close:
            db.close()


def approve_ticket(db: Session, ticket: Ticket, approver: str, comment: str = "") -> Ticket:
    if ticket.status != TicketStatus.pending_approval.value:
        raise ValueError("当前状态不可审批")
    approval = next((a for a in ticket.approvals if a.status == "pending"), None)
    if approval is None:
        raise ValueError("没有待处理的审批单")
    now = utcnow()
    exp = approval.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        raise ValueError("审批已过期，需重新审批")
    expected = params_digest(ticket.asset_id, ticket.playbook_version or "", ticket.params or {})
    if expected != approval.params_digest or expected != ticket.params_digest:
        raise ValueError("参数或作业版本已变化，审批绑定失效")
    if approval.asset_id != ticket.asset_id:
        raise ValueError("审批绑定的资产不匹配")
    approval.status = "approved"
    approval.approver = approver
    approval.comment = comment
    approval.decided_at = now
    if ticket.approval_requested_at:
        req = ticket.approval_requested_at
        if req.tzinfo is None:
            req = req.replace(tzinfo=timezone.utc)
        ticket.human_wait_seconds = (now - req).total_seconds()
    _set_status(ticket, TicketStatus.pending_execution)
    add_event(db, ticket_id=ticket.id, kind="approval_granted", actor=approver, message=f"{approver} 已批准")
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="approval",
        actor=approver,
        approver=approver,
        params_digest=ticket.params_digest,
        playbook_version=ticket.playbook_version,
        result={"decision": "approved", "comment": comment},
    )
    db.commit()
    dispatch_execution(ticket.id)
    return ticket


def reject_ticket(db: Session, ticket: Ticket, approver: str, comment: str = "") -> Ticket:
    if ticket.status != TicketStatus.pending_approval.value:
        raise ValueError("当前状态不可审批")
    approval = next((a for a in ticket.approvals if a.status == "pending"), None)
    if approval is not None:
        approval.status = "rejected"
        approval.approver = approver
        approval.comment = comment
        approval.decided_at = utcnow()
    add_event(db, ticket_id=ticket.id, kind="approval_rejected", actor=approver, message=f"{approver} 驳回：{comment}")
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="approval",
        actor=approver,
        approver=approver,
        params_digest=ticket.params_digest,
        result={"decision": "rejected", "comment": comment},
    )
    _escalate(db, ticket, f"审批驳回：{comment or '无备注'}")
    db.commit()
    return ticket


def _request_approval(db: Session, ticket: Ticket, playbook_id: str) -> None:
    _set_status(ticket, TicketStatus.pending_approval)
    ticket.approval_requested_at = utcnow()
    db.add(
        Approval(
            ticket_id=ticket.id,
            asset_id=ticket.asset_id,
            playbook_id=playbook_id,
            playbook_version=ticket.playbook_version or "",
            params_digest=ticket.params_digest or "",
            expires_at=utcnow() + timedelta(hours=24),
        )
    )
    add_event(db, ticket_id=ticket.id, kind="approval_requested", message="高风险动作等待审批，任务单已挂起")


def retry_queued_executions() -> int:
    from app.database import SessionLocal

    db = SessionLocal()
    n = 0
    try:
        tickets = db.scalars(
            select(Ticket).where(Ticket.status == TicketStatus.pending_execution.value)
        ).all()
        ids = []
        from app.services.locks import get_lock

        for t in tickets:
            lock = get_lock(db, t.asset_id)
            if lock is None or lock.ticket_id == t.id:
                ids.append(t.id)
        db.commit()
    finally:
        db.close()
    for tid in ids:
        dispatch_execution(tid)
        n += 1
    return n


def _escalate(db: Session, ticket: Ticket, reason: str) -> None:
    if ticket.status in {TicketStatus.escalated.value, TicketStatus.recovered.value, TicketStatus.skipped.value}:
        ticket.escalate_reason = reason
        return
    ticket.escalate_reason = reason
    ticket.closed_at = utcnow()
    _set_status(ticket, TicketStatus.escalated)
    add_event(db, ticket_id=ticket.id, kind="escalated", message=reason, payload={"reason": reason})
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="escalated",
        result={"reason": reason},
        params_digest=ticket.params_digest,
        playbook_version=ticket.playbook_version,
        evidence_refs=(ticket.diagnosis or {}).get("evidence_refs") or [],
    )
    notify_ticket(db, ticket, "escalated", f"{ticket.number} 已升级：{reason}")


def _evidence_refs(evidence: dict[str, Any]) -> list[str]:
    refs = []
    for key, val in evidence.items():
        if isinstance(val, dict) and val.get("ref"):
            refs.append(val["ref"])
        elif key:
            refs.append(key)
    return refs
