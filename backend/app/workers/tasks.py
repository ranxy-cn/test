from app.workers.celery_app import celery_app
from app.services.pipeline import run_execution, run_pipeline


@celery_app.task(name="investigate_ticket", max_retries=0)
def investigate_ticket(ticket_id: int) -> None:
    run_pipeline(ticket_id)


@celery_app.task(name="execute_ticket", max_retries=0)
def execute_ticket(ticket_id: int) -> None:
    run_execution(ticket_id)
