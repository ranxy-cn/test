from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.integrations import integration_health
from app.models import BackupJob, BackupRun, Ticket, TicketStatus
from app.routers.deps import require_perm
from app.services import stress
from app.services.backups import backup_report, run_backup, verify_restore
from app.services.locks import hold_demo_lock, list_locks, release_asset_lock
from app.services.notify import list_notifications, mark_read
from app.services.pipeline import dispatch_execution, retry_queued_executions

router = APIRouter(prefix="/api/v1")


class LockIn(BaseModel):
    asset_id: str
    ttl_seconds: int = 60


class StressIn(BaseModel):
    duration_seconds: int = 420


def _require_stress_enabled() -> None:
    if not get_settings().stress_tools_enabled:
        raise HTTPException(
            403,
            "CPU 压测工具未开启：仅服务器部署（有真实 Zabbix）设置 STRESS_TOOLS_ENABLED=true 后可用",
        )


@router.get("/tools/cpu-stress")
def api_stress_status():
    result = stress.status()
    result["enabled"] = get_settings().stress_tools_enabled
    result["max_seconds"] = get_settings().stress_max_seconds
    return result


@router.post("/tools/cpu-stress")
def api_stress_start(body: StressIn):
    _require_stress_enabled()
    return stress.start(body.duration_seconds)


@router.post("/tools/cpu-stress/stop")
def api_stress_stop():
    _require_stress_enabled()
    return stress.stop()


@router.get("/status", dependencies=[Depends(require_perm("status:read"))])
def integration_status():
    return integration_health()


@router.get("/locks", dependencies=[Depends(require_perm("tickets:read"))])
def api_list_locks(db: Session = Depends(get_db)):
    rows = list_locks(db)
    db.commit()
    return {
        "items": [
            {
                "asset_id": r.asset_id,
                "ticket_id": r.ticket_id,
                "holder": r.holder,
                "expires_at": r.expires_at,
                "heartbeat_at": r.heartbeat_at,
            }
            for r in rows
        ]
    }


@router.post("/locks", dependencies=[Depends(require_perm("tickets:operate"))])
def api_hold_lock(body: LockIn, db: Session = Depends(get_db)):
    lock = hold_demo_lock(db, body.asset_id, body.ttl_seconds)
    db.commit()
    return {"asset_id": lock.asset_id, "ticket_id": lock.ticket_id, "expires_at": lock.expires_at, "holder": lock.holder}


@router.delete("/locks/{asset_id}", dependencies=[Depends(require_perm("tickets:operate"))])
def api_release_lock(asset_id: str, db: Session = Depends(get_db)):
    release_asset_lock(db, asset_id)
    db.commit()
    return {"ok": True, "asset_id": asset_id}


@router.post("/tickets/{ticket_id}/retry-execution")
def api_retry(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(404, "任务单不存在")
    if ticket.status != TicketStatus.pending_execution.value:
        raise HTTPException(409, "当前状态不可重试执行")
    db.commit()
    dispatch_execution(ticket.id)
    return {"ok": True, "ticket_id": ticket.id}


@router.post("/locks/retry-queued", dependencies=[Depends(require_perm("tickets:operate"))])
def api_retry_queued():
    return {"retried": retry_queued_executions()}


@router.get("/notifications", dependencies=[Depends(require_perm("notifications:read"))])
def api_notifications(unread: bool = False, ticket_id: int | None = None, db: Session = Depends(get_db)):
    rows = list_notifications(db, unread_only=unread, ticket_id=ticket_id)
    return {
        "items": [
            {
                "id": n.id,
                "ticket_id": n.ticket_id,
                "kind": n.kind,
                "channel": n.channel,
                "title": n.title,
                "body": n.body,
                "read": n.read,
                "created_at": n.created_at,
            }
            for n in rows
        ]
    }


@router.post("/notifications/{notif_id}/read", dependencies=[Depends(require_perm("notifications:read"))])
def api_mark_read(notif_id: int, db: Session = Depends(get_db)):
    row = mark_read(db, notif_id)
    if row is None:
        raise HTTPException(404, "通知不存在")
    db.commit()
    return {"ok": True, "id": row.id}


@router.get("/backups")
def api_backups(db: Session = Depends(get_db)):
    return backup_report(db)


@router.get("/backups/runs", dependencies=[Depends(require_perm("backups:read"))])
def api_backup_runs(db: Session = Depends(get_db)):
    rows = db.scalars(select(BackupRun).order_by(BackupRun.id.desc()).limit(50)).all()
    return {
        "items": [
            {
                "id": r.id,
                "job_id": r.job_id,
                "status": r.status,
                "backup_ok": r.backup_ok,
                "restore_verified": r.restore_verified,
                "note": r.note,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
            }
            for r in rows
        ]
    }


@router.post("/backups/{job_id}/run")
def api_run_backup(job_id: str, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(404, "备份任务不存在")
    run = run_backup(db, job)
    db.commit()
    return {
        "run_id": run.id,
        "job_id": job.id,
        "backup_ok": run.backup_ok,
        "restore_verified": run.restore_verified,
        "status": run.status,
        "note": run.note,
    }


@router.post("/backups/{job_id}/verify-restore", dependencies=[Depends(require_perm("backups:operate"))])
def api_verify_restore(job_id: str, db: Session = Depends(get_db)):
    job = db.get(BackupJob, job_id)
    if job is None:
        raise HTTPException(404, "备份任务不存在")
    run = verify_restore(db, job)
    db.commit()
    return {
        "run_id": run.id,
        "job_id": job.id,
        "backup_ok": job.last_backup_ok,
        "restore_verified": run.restore_verified,
        "status": run.status,
        "note": run.note,
    }


@router.post("/backups/run-due", dependencies=[Depends(require_perm("backups:operate"))])
def api_run_due(db: Session = Depends(get_db)):
    jobs = db.scalars(select(BackupJob).where(BackupJob.enabled.is_(True))).all()
    runs = []
    for job in jobs:
        if job.last_backup_ok is None:
            runs.append(run_backup(db, job).id)
    db.commit()
    return {"ran": runs}
