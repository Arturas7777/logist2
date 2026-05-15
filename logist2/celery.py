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
        # Раньше было */1 — это создавало нагрузку на Gmail API и Celery
        # worker (60 запусков/час даже когда новой почты нет). */2 даёт
        # тот же UX (письма видны в течение пары минут), но в 2 раза
        # меньше запусков и риск 429 от Gmail.
        'schedule': crontab(minute='*/2'),
    },
    'check-business-rules-daily': {
        # Аудит 3 бизнес-правил (FACT/AV/PARDP). При превышении baseline
        # логируется warning → Sentry создаёт issue. См. core/tasks.py
        # → check_business_rules и docs/accounting_session_handoff.md.
        'task': 'core.tasks.check_business_rules',
        'schedule': crontab(hour=8, minute=15),
    },
    'check-revolut-jwt-expiry-daily': {
        # JWT-assertion для Revolut живёт ~90 дней (см. setup_revolut.py).
        # Когда он истекает, refresh access_token возвращает 401 и вся
        # синхронизация падает. Эта задача алертит за 14 дней до истечения,
        # чтобы успеть запустить regenerate_revolut_jwt.
        'task': 'core.tasks.check_revolut_jwt_expiry',
        'schedule': crontab(hour=9, minute=0),
    },
}
