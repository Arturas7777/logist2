import os

from celery import Celery
from celery.schedules import crontab

# Local default = dev. На сервере scripts/celery.service и celerybeat.service
# явно выставляют DJANGO_SETTINGS_MODULE=logist2.settings.prod через
# Environment= — до того, как этот модуль импортируется celery worker'ом.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings.dev')
app = Celery('logist2')
app.config_from_object('django.conf:settings', namespace='CELERY')
# autodiscover_tasks ищет только 'tasks.py' в каждом INSTALLED_APP.
# Явно перечисляем нестандартные модули задач.
app.autodiscover_tasks(related_name='tasks')
app.autodiscover_tasks(related_name='tasks_email')
app.autodiscover_tasks(related_name='tasks_monitoring')

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
    'process-telegram-starts': {
        # Привязка chat_id клиентов по персональным ссылкам ?start=<token>.
        # Клиент жмёт Start по своей ссылке — задача находит его по токену и
        # сохраняет chat_id (идемпотентно, бот отвечает подтверждением).
        'task': 'core.tasks.process_telegram_starts_task',
        'schedule': crontab(minute='*'),
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
    # ── System monitoring (/admin/system-monitor/) ─────────────────────────
    # Снимок CPU/RAM/disk/процессов/Postgres/Redis в БД. 288 строк/день,
    # ~8 600 за месяц при retention=30 дней — копейки по размеру.
    'collect-system-metrics': {
        'task': 'core.tasks_monitoring.collect_system_metrics',
        'schedule': crontab(minute='*/5'),
    },
    # Пинг /health/ для расчёта SLA-аптайма.
    'ping-uptime': {
        'task': 'core.tasks_monitoring.ping_uptime',
        'schedule': crontab(minute='*'),
    },
    # Удаление метрик старше MONITORING_RETENTION_DAYS (по дефолту 30 дней).
    'cleanup-old-metrics-daily': {
        'task': 'core.tasks_monitoring.cleanup_old_metrics',
        'schedule': crontab(hour=4, minute=0),
    },
    # Проверка свежести ночного PostgreSQL-бэкапа. Ночной cron делает
    # /var/backups/logist2/${DB_NAME}_YYYY-MM-DD.dump в 03:30, эта задача
    # в 04:15 убеждается, что свежий .dump существует и не старше 36 часов.
    # См. scripts/server_pg_backup.sh и docs/BACKUPS.md.
    'check-backup-freshness-daily': {
        'task': 'core.tasks_monitoring.check_backup_freshness',
        'schedule': crontab(hour=4, minute=15),
    },
}
