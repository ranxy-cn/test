from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import BackupJob, BackupRun, utcnow
from app.services.audit import add_audit


def seed_backup_jobs(db: Session) -> None:
    jobs = [
        BackupJob(
            id="bj-order-db-daily",
            name="订单库每日备份",
            asset_id="ast-order-db-01",
            schedule="daily-02:00",
            note="引擎原生备份占位；备份成功与恢复验证分开记",
        ),
        BackupJob(
            id="bj-order-app-files",
            name="订单应用文件备份",
            asset_id="ast-order-app-01",
            schedule="daily-03:00",
            note="restic 占位，一期后接入真实仓库",
        ),
    ]
    for job in jobs:
        if db.get(BackupJob, job.id) is None:
            db.add(job)
    db.flush()


def run_backup(db: Session, job: BackupJob, *, restore_verify: bool = False) -> BackupRun:
    now = utcnow()
    run = BackupRun(
        job_id=job.id,
        status="running",
        started_at=now,
        note="mock 备份：未写入真实介质",
    )
    db.add(run)
    db.flush()
    run.backup_ok = True
    run.restore_verified = True if restore_verify else None
    run.status = "backup_ok" if not restore_verify else "verified"
    run.finished_at = utcnow()
    run.note = "mock 备份完成；恢复验证未执行" if not restore_verify else "mock 备份完成且隔离恢复样例通过"
    job.last_run_at = run.finished_at
    job.last_backup_ok = True
    if restore_verify:
        job.last_restore_verified = True
    add_audit(
        db,
        ticket_id=None,
        event_type="backup_run",
        result={
            "job_id": job.id,
            "run_id": run.id,
            "backup_ok": run.backup_ok,
            "restore_verified": run.restore_verified,
        },
    )
    db.flush()
    return run


def verify_restore(db: Session, job: BackupJob) -> BackupRun:
    now = utcnow()
    run = BackupRun(
        job_id=job.id,
        backup_ok=job.last_backup_ok,
        restore_verified=True,
        status="restore_verified",
        note="mock 隔离恢复 + 样例查询通过；不等于生产已切换",
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    job.last_restore_verified = True
    job.last_run_at = now
    add_audit(
        db,
        ticket_id=None,
        event_type="backup_restore_verify",
        result={"job_id": job.id, "restore_verified": True, "backup_ok": job.last_backup_ok},
    )
    db.flush()
    return run


def backup_report(db: Session) -> dict:
    from sqlalchemy import select

    jobs = db.scalars(select(BackupJob)).all()
    ran = [j for j in jobs if j.last_backup_ok is not None]
    verified = [j for j in jobs if j.last_restore_verified is True]
    unchecked = [j for j in jobs if j.last_backup_ok is None]
    status = "未运行"
    if ran:
        status = "已备份"
        if verified:
            status = "部分已验证" if len(verified) < len(jobs) else "备份且已验证"
    return {
        "jobs": len(jobs),
        "checked": len(ran) > 0,
        "status": status,
        "backup_ok_count": len([j for j in ran if j.last_backup_ok]),
        "restore_verified_count": len(verified),
        "unchecked_count": len(unchecked),
        "items": [
            {
                "id": j.id,
                "name": j.name,
                "asset_id": j.asset_id,
                "last_backup_ok": j.last_backup_ok,
                "last_restore_verified": j.last_restore_verified,
                "last_run_at": j.last_run_at,
                "backup_status": "成功" if j.last_backup_ok else ("失败" if j.last_backup_ok is False else "未运行"),
                "restore_status": "已验证" if j.last_restore_verified else "未验证",
            }
            for j in jobs
        ],
        "note": "备份成功 ≠ 恢复验证成功。未运行/未检查不得写成成功。",
    }
