import logging

from app.workers.celery_app import LoggedTask, celery_app
from app.services.pipeline import retry_queued_executions, run_execution, run_pipeline

log = logging.getLogger("devops.tasks")


@celery_app.task(name="investigate_ticket", base=LoggedTask, max_retries=0)
def investigate_ticket(ticket_id: int) -> None:
    run_pipeline(ticket_id)


@celery_app.task(name="execute_ticket", base=LoggedTask, max_retries=0)
def execute_ticket(ticket_id: int) -> None:
    run_execution(ticket_id)


@celery_app.task(name="retry_queued_tickets", base=LoggedTask, max_retries=0)
def retry_queued_tickets() -> int:
    return retry_queued_executions()


@celery_app.task(name="probe_assets", base=LoggedTask, max_retries=0)
def probe_assets() -> dict:
    """定时探活全部资产：TCP 探测，回写 reachable/last_seen_at/unreachable_reason。"""
    from app.database import SessionLocal
    from app.services.probe import run_probe_cycle

    db = SessionLocal()
    try:
        stats = run_probe_cycle(db)
        log.info("资产探活完成 %s", stats)
        return stats
    except Exception:
        log.exception("资产探活失败")
        raise
    finally:
        db.close()


@celery_app.task(name="collect_metric_samples", base=LoggedTask, max_retries=0)
def collect_metric_samples() -> dict:
    """定时采集全部资产 CPU/内存/磁盘/负载并落库（5 分钟槽幂等），支撑趋势/对比/基线/预测。"""
    from app.database import SessionLocal
    from app.services.metrics_store import run_collect_cycle

    db = SessionLocal()
    try:
        stats = run_collect_cycle(db)
        log.info("指标采样完成 %s", stats)
        return stats
    except Exception:
        log.exception("指标采样失败")
        raise
    finally:
        db.close()


@celery_app.task(name="scan_alerts", base=LoggedTask, max_retries=0)
def scan_alerts() -> dict:
    """定时告警扫描：按子机/母机策略判定指标「越限持续满窗口」→ 异常事件（站内提示）。"""
    from app.database import SessionLocal
    from app.logging_setup import task_var
    from app.services.alert_engine import run_alert_cycle

    token = task_var.set("scan_alerts")
    db = SessionLocal()
    try:
        stats = run_alert_cycle(db)
        log.info(
            "告警扫描完成 checked=%s triggered=%s recovered=%s re_alerted=%s diag_snapshot=%s",
            stats.get("checked"), stats.get("triggered"), stats.get("recovered"),
            stats.get("re_alerted"), stats.get("diag_snapshot"),
        )
        return stats
    except Exception:
        log.exception("告警扫描失败")
        raise
    finally:
        task_var.reset(token)
        db.close()


@celery_app.task(name="analyze_anomaly_logs", base=LoggedTask, bind=True, max_retries=3)
def analyze_anomaly_logs(self, anomaly_id: int) -> str:
    """AI 日志分析任务：告警联动/手动触发入口。

    异步执行不阻塞告警主流程；失败指数退避重试（30/60/120s），重试耗尽由
    ai_analyzer.run_analysis 落库 failed 状态并写审计，系统降级为仅告警无分析。
    """
    from app.database import SessionLocal
    from app.logging_setup import task_var
    from app.services.ai_analyzer import run_analysis

    token = task_var.set("ai_analysis")
    try:
        run_analysis(anomaly_id)
    except Exception as exc:
        if self.request.retries < 3:
            log.warning("AI 分析失败准备重试 anomaly_id=%s retry=%s", anomaly_id, self.request.retries + 1)
            raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
        log.exception("AI 分析重试耗尽 anomaly_id=%s", anomaly_id)
    finally:
        task_var.reset(token)
    return "done"


@celery_app.task(name="run_due_backups", base=LoggedTask, max_retries=0)
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
    except Exception:
        log.exception("备份任务失败")
        raise
    finally:
        db.close()
    return n
