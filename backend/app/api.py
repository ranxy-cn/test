from __future__ import annotations

import hmac
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import authenticate
from app.config import get_settings
from app.database import get_db
from app.domain.catalog import dump_catalog
from app.models import (
    AlertEvent,
    Asset,
    AuditLog,
    DigitalEmployee,
    Notification,
    Ticket,
    TicketEvent,
    TicketStatus,
    utcnow,
)
from app.schemas import ApprovalIn, LoginIn, TicketEventOut, TicketOut, ZabbixWebhookIn
from app.services.audit import add_audit, add_event
from app.services.notify import notify_ticket
from app.services.pipeline import approve_ticket, dispatch_investigation, in_maintenance, reject_ticket
from app.services.tickets import make_idempotency_key, next_ticket_number
from app.integrations.zabbix.mapping import find_asset_by_zabbix

router = APIRouter()


@router.get("/health")
def health():
    from app.integrations import describe_integrations

    return {
        "ok": True,
        "service": "devops-agent",
        "employee": get_settings().employee_id,
        "integrations": describe_integrations(),
    }


@router.get("/api/v1/playbooks")
def playbooks():
    return {"items": dump_catalog()}


@router.get("/api/v1/employee/{employee_id}")
def employee_profile(employee_id: str, db: Session = Depends(get_db)):
    row = db.get(DigitalEmployee, employee_id)
    if row is None:
        raise HTTPException(404, "智能巡检不存在")

    total = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.employee_id == employee_id)) or 0
    recovered = db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.employee_id == employee_id, Ticket.status == TicketStatus.recovered.value
        )
    ) or 0
    return {
        "id": row.id,
        "name": row.name,
        "team": row.team,
        "systems": row.systems,
        "manager": row.manager,
        "oncall": row.oncall,
        "skill_version": row.skill_version,
        "auth_expires_at": row.auth_expires_at,
        "status": row.status,
        "duties": row.duties,
        "slogan": "LLM 只做分析与建议 · 策略引擎做决定 · 执行器做动作 · 证据链做证明",
        "stats": {"tickets": total, "recovered": recovered},
    }


@router.get("/api/v1/assets")
def list_assets(db: Session = Depends(get_db)):
    rows = db.scalars(select(Asset).order_by(Asset.id)).all()
    return {
        "items": [
            {
                "id": a.id,
                "hostname": a.hostname,
                "app": a.app,
                "role": a.role,
                "env": a.env,
                "owner": a.owner,
                "tenant_id": a.tenant_id,
                "reachable": a.reachable,
                "db_ok": a.db_ok,
                "external_id": a.external_id,
                "zabbix_host": a.zabbix_host,
            }
            for a in rows
        ]
    }


@router.post("/api/v1/auth/login")
def login(body: LoginIn):
    token, ttl, username = authenticate(body.username, body.password)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "username": username,
    }


def _check_webhook_secret(
    x_webhook_secret: str | None = Header(default=None),
    x_zabbix_token: str | None = Header(default=None, alias="X-Zabbix-Token"),
    authorization: str | None = Header(default=None),
) -> None:
    provided = (x_webhook_secret or x_zabbix_token or "").strip()
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(" ", 1)[1].strip()
    expected = get_settings().webhook_secret
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "Webhook 鉴权失败")


@router.post("/api/v1/webhooks/zabbix")
def zabbix_webhook(
    body: ZabbixWebhookIn,
    db: Session = Depends(get_db),
    _: None = Depends(_check_webhook_secret),
):
    asset = find_asset_by_zabbix(
        db,
        asset_id=body.asset_id,
        host=body.host,
        hostname=body.hostname,
        hostid=body.hostid,
    )
    if asset is None:
        raise HTTPException(404, f"未知资产 {body.asset_id or body.host or body.hostid}")
    if asset.tenant_id != get_settings().tenant_id:
        raise HTTPException(403, "拒绝跨租户目标")

    key = make_idempotency_key(body.event_id, asset.id, body.job_version, body.action_type)
    existing = db.scalar(select(Ticket).where(Ticket.idempotency_key == key))
    if existing is not None:
        add_event(db, ticket_id=existing.id, kind="dedup_merged", message=f"重复告警已合并 event_id={body.event_id}")
        add_audit(db, ticket_id=existing.id, event_type="dedup", result={"event_id": body.event_id, "key": key})
        db.commit()
        return {"duplicate": True, "ticket": TicketOut.model_validate(existing)}

    skipped = in_maintenance(db, asset.id)
    scenario = body.demo_scenario or _infer_scenario(body.trigger_name)

    alert = AlertEvent(
        idempotency_key=key,
        event_id=body.event_id,
        asset_id=asset.id,
        payload=body.model_dump(),
        skipped=bool(skipped),
        skip_reason="maintenance_window" if skipped else None,
    )
    db.add(alert)
    db.flush()

    if skipped:
        add_audit(
            db,
            ticket_id=None,
            event_type="maintenance_skip",
            result={"asset_id": asset.id, "reason": skipped.reason, "event_id": body.event_id},
        )
        db.commit()
        return {
            "skipped": True,
            "reason": "maintenance_window",
            "detail": skipped.reason,
            "ticket": None,
        }

    ticket = Ticket(
        number=next_ticket_number(db),
        idempotency_key=key,
        source="alert",
        status=TicketStatus.pending_analysis.value,
        employee_id=get_settings().employee_id,
        asset_id=asset.id,
        tenant_id=asset.tenant_id,
        title=body.message or body.trigger_name,
        trigger_name=body.trigger_name,
        severity=body.severity,
        action_type=body.action_type,
        job_version=body.job_version,
        event_id=body.event_id,
        owner=asset.owner,
        demo_scenario=scenario,
    )
    db.add(ticket)
    db.flush()
    alert.ticket_id = ticket.id
    add_event(db, ticket_id=ticket.id, kind="alert_received", message=f"Zabbix 告警立案 {ticket.number}")
    add_audit(
        db,
        ticket_id=ticket.id,
        event_type="ticket_created",
        result={"number": ticket.number, "asset_id": asset.id},
    )
    notify_ticket(db, ticket, "ticket_created", f"{ticket.number} 已立案，正在排查")
    db.commit()
    dispatch_investigation(ticket.id)
    db.refresh(ticket)
    return {"duplicate": False, "skipped": False, "ticket": TicketOut.model_validate(ticket)}


def _infer_scenario(trigger: str) -> str:
    t = trigger.lower()
    if "replication" in t or "主备" in t or "failover" in t:
        return "yellow"
    if "未知" in t or "mystery" in t or "native" in t:
        return "red"
    if "verify" in t or "探测失败" in t:
        return "verify_fail"
    return "green"


@router.get("/api/v1/tickets")
def list_tickets(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Ticket).order_by(Ticket.id.desc())
    if status:
        stmt = stmt.where(Ticket.status == status)
    rows = db.scalars(stmt).all()
    return {"items": [TicketOut.model_validate(t) for t in rows]}


@router.get("/api/v1/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.scalar(
        select(Ticket).options(selectinload(Ticket.approvals)).where(Ticket.id == ticket_id)
    )
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    events = db.scalars(
        select(TicketEvent).where(TicketEvent.ticket_id == ticket_id).order_by(TicketEvent.id.asc())
    ).all()
    audits = db.scalars(
        select(AuditLog).where(AuditLog.ticket_id == ticket_id).order_by(AuditLog.id.asc())
    ).all()
    from app.services.locks import get_lock

    lock = get_lock(db, ticket.asset_id)
    notes = db.scalars(
        select(Notification).where(Notification.ticket_id == ticket_id).order_by(Notification.id.asc())
    ).all()
    return {
        "ticket": TicketOut.model_validate(ticket),
        "events": [TicketEventOut.model_validate(e) for e in events],
        "approvals": [
            {
                "id": a.id,
                "status": a.status,
                "asset_id": a.asset_id,
                "playbook_id": a.playbook_id,
                "playbook_version": a.playbook_version,
                "params_digest": a.params_digest,
                "approver": a.approver,
                "comment": a.comment,
                "expires_at": a.expires_at,
            }
            for a in ticket.approvals
        ],
        "audit": [
            {
                "id": a.id,
                "event_type": a.event_type,
                "actor": a.actor,
                "model_version": a.model_version,
                "policy_version": a.policy_version,
                "playbook_version": a.playbook_version,
                "approver": a.approver,
                "params_digest": a.params_digest,
                "evidence_refs": a.evidence_refs,
                "result": a.result,
                "created_at": a.created_at,
            }
            for a in audits
        ],
        "lock": None
        if lock is None
        else {
            "asset_id": lock.asset_id,
            "ticket_id": lock.ticket_id,
            "holder": lock.holder,
            "expires_at": lock.expires_at,
            "heartbeat_at": lock.heartbeat_at,
        },
        "notifications": [
            {
                "id": n.id,
                "kind": n.kind,
                "channel": n.channel,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at,
            }
            for n in notes
        ],
    }


@router.post("/api/v1/tickets/{ticket_id}/approve")
def api_approve(ticket_id: int, body: ApprovalIn, db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    try:
        approve_ticket(db, ticket, body.approver, body.comment)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(ticket)
    return {"ticket": TicketOut.model_validate(ticket)}


@router.post("/api/v1/tickets/{ticket_id}/reject")
def api_reject(ticket_id: int, body: ApprovalIn, db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    try:
        reject_ticket(db, ticket, body.approver, body.comment)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.refresh(ticket)
    return {"ticket": TicketOut.model_validate(ticket)}


@router.get("/api/v1/audit")
def list_audit(ticket_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(200)
    if ticket_id is not None:
        stmt = select(AuditLog).where(AuditLog.ticket_id == ticket_id).order_by(AuditLog.id.asc())
    rows = db.scalars(stmt).all()
    return {
        "items": [
            {
                "id": a.id,
                "ticket_id": a.ticket_id,
                "event_type": a.event_type,
                "actor": a.actor,
                "model_version": a.model_version,
                "policy_version": a.policy_version,
                "playbook_version": a.playbook_version,
                "approver": a.approver,
                "params_digest": a.params_digest,
                "evidence_refs": a.evidence_refs,
                "result": a.result,
                "created_at": a.created_at,
            }
            for a in rows
        ]
    }


@router.get("/api/v1/reports/daily")
def daily_report(report_date: date | None = Query(default=None, alias="date"), db: Session = Depends(get_db)):
    day = report_date or utcnow().date()

    def day_of(dt: datetime | None):
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()

    tickets = [t for t in db.scalars(select(Ticket)).all() if day_of(t.created_at) == day]
    by_status: dict[str, int] = {}
    for t in tickets:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    recovered = [t for t in tickets if t.status == TicketStatus.recovered.value]
    escalated = [t for t in tickets if t.status == TicketStatus.escalated.value]
    open_items = [
        t
        for t in tickets
        if t.status
        not in {TicketStatus.recovered.value, TicketStatus.escalated.value, TicketStatus.skipped.value}
    ]
    employee = db.get(DigitalEmployee, get_settings().employee_id)
    from app.services.backups import backup_report

    backups = backup_report(db)
    return {
        "date": day.isoformat(),
        "employee_id": get_settings().employee_id,
        "employee_name": employee.name if employee else "",
        "systems": employee.systems if employee else ["订单系统"],
        "planned": len(tickets),
        "completed": len(recovered),
        "by_status": by_status,
        "anomalies": [
            {
                "number": t.number,
                "asset_id": t.asset_id,
                "title": t.title,
                "status": t.status,
                "root_cause": (t.diagnosis or {}).get("root_cause"),
                "policy_light": t.policy_light,
            }
            for t in tickets
        ],
        "backups": backups,
        "open_items": [
            {
                "number": t.number,
                "id": t.id,
                "owner": t.owner,
                "status": t.status,
                "escalate_reason": t.escalate_reason,
                "evidence": True if t.evidence else False,
            }
            for t in open_items + escalated
            if t.status != TicketStatus.recovered.value
        ],
        "note": "「已通知人工」不等于「业务已恢复」。失败不得写成功。",
    }
