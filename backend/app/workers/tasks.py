from app.workers.celery_app import celery_app
from app.services.pipeline import retry_queued_executions, run_execution, run_pipeline


@celery_app.task(name="investigate_ticket", max_retries=0)
def investigate_ticket(ticket_id: int) -> None:
    run_pipeline(ticket_id)


@celery_app.task(name="execute_ticket", max_retries=0)
def execute_ticket(ticket_id: int) -> None:
    run_execution(ticket_id)


@celery_app.task(name="retry_queued_tickets", max_retries=0)
def retry_queued_tickets() -> int:
    return retry_queued_executions()


@celery_app.task(name="probe_assets", max_retries=0)
def probe_assets() -> dict:
    """定时探活全部资产：TCP 探测，回写 reachable/last_seen_at/unreachable_reason。"""
    from app.database import SessionLocal
    from app.services.probe import run_probe_cycle

    db = SessionLocal()
    try:
        return run_probe_cycle(db)
    finally:
        db.close()


@celery_app.task(name="run_due_backups", max_retries=0)
def run_due_backups() -> int:
    from app.database import SessionLocal
    from sqlalchemy import select
    from app.models import BackupJob
    from app.services.backups import run_backup

    db = SessionLocal()
    n = 0
    try:
        jobs = db.scalars(select(BackupJob).where(BackupJob.enabled.is_(True))).all()
        for job in jobs:
            if job.last_backup_ok is None:
                run_backup(db, job)
                n += 1
        db.commit()
    finally:
        db.close()
    return n
