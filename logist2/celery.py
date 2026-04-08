import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')
app = Celery('logist2')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

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
}
