from celery import Celery
from celery.signals import setup_logging as celery_setup_logging

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "devops_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)


@celery_setup_logging.connect
def _setup_worker_logging(**_):
    """接管 worker/beat 日志：与 API 同一套结构化格式（含 pid/tid/task 上下文）。"""
    from app.logging_setup import setup_logging

    setup_logging("devops-worker")
    return True  # 阻断 celery 默认日志安装


class LoggedTask(celery_app.Task):
    """任务基类：任何任务抛出未捕获异常时输出结构化堆栈（含 task_id 与参数）。"""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        import logging

        logging.getLogger("devops.tasks").error(
            "任务失败 %s task_id=%s args=%r kwargs=%r",
            self.name, task_id, args, kwargs,
            exc_info=(type(exc), exc, None),
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)


celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    beat_schedule={
        "retry-queued-tickets": {
            "task": "retry_queued_tickets",
            "schedule": 15.0,
        },
        "run-due-backups": {
            "task": "run_due_backups",
            "schedule": 60.0,
        },
        "probe-assets": {
            "task": "probe_assets",
            "schedule": 300.0,
        },
        # 监控指标采样：每 5 分钟一帧，落库支撑 24h 趋势/多日对比/基线异常/预测
        "collect-metric-samples": {
            "task": "collect_metric_samples",
            "schedule": 300.0,
        },
        # 指标越限告警：每分钟按子机/母机策略判定「越限持续满窗口」→ 异常事件（站内提示）
        "scan-alerts": {
            "task": "scan_alerts",
            "schedule": 60.0,
        },
    },
)
