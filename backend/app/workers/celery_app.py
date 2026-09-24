from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "devops_agent",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
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
    },
)
