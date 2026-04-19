import os

from celery import Celery
from celery.schedules import crontab

# Fallback only: on the server systemd units MUST export
# DJANGO_SETTINGS_MODULE=logist2.settings.prod (see scripts/celery.service).
# Locally dev can override via `set DJANGO_SETTINGS_MODULE=logist2.settings.dev`.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
app = Celery('logist2')
app.config_from_object('django.conf:settings', namespace='CELERY')
# autodiscover_tasks ищет только 'tasks.py' в каждом INSTALLED_APP.
# Явно перечисляем нестандартные модули задач.
app.autodiscover_tasks(related_name='tasks')
app.autodiscover_tasks(related_name='tasks_email')

app.conf.beat_schedule = {
    'check-overdue-invoices-daily': {
        'task': 'core.tasks.check_overdue_invoices',
        'schedule': crontab(hour=6, minute=0),
    },
    'check-balance-consistency-weekly': {
        'task': 'core.tasks.check_balance_consistency',
        'schedule': crontab(hour=3, minute=0, day_of_week='sunday'),
    },
    'sync-sitepro-invoices-daily': {
        'task': 'core.tasks.sync_sitepro_invoices',
        'schedule': crontab(hour=7, minute=30),
    },
    'sync-bank-and-reconcile': {
        'task': 'core.tasks.sync_bank_and_reconcile',
        'schedule': crontab(minute='*/30'),
    },
    'sync-emails-from-gmail': {
        'task': 'core.tasks_email.sync_emails_from_gmail',
        'schedule': crontab(minute='*/1'),
    },
}
