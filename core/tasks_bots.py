"""Утренний снимок для Главного бота."""

from celery import shared_task


@shared_task
def bot_morning_snapshot_task():
    from core.services.bot_ops import store_morning_job

    job = store_morning_job()
    return {"job_id": job.pk}
