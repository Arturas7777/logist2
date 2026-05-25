from .base import *

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '*']

LOGGING['loggers']['django']['level'] = 'DEBUG'
LOGGING['loggers']['core']['level'] = 'DEBUG'

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Celery в dev: выполняем задачи синхронно прямо в процессе runserver,
# чтобы не запускать отдельный worker. Это даёт мгновенный feedback при
# тестировании AI-сканов / sitepro-sync / email-уведомлений.
# Если хочешь полноценный worker (например, для нагрузочного теста или
# проверки concurrency) — закомментируй эти две строки и запусти:
#   celery -A logist2 worker -l info --pool=solo  (Windows)
import os as _os

if _os.getenv('CELERY_TASK_ALWAYS_EAGER', '1') == '1':
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# Опционально включить django-debug-toolbar в dev-режиме.
# Установка:  pip install django-debug-toolbar
# Активация:  USE_DEBUG_TOOLBAR=1 в .env
# Зачем:      сразу видеть N+1, медленные SQL и кэш-промахи.
import os as _os

if _os.getenv('USE_DEBUG_TOOLBAR') == '1':
    try:
        import debug_toolbar  # noqa: F401
        if 'debug_toolbar' not in INSTALLED_APPS:
            INSTALLED_APPS.append('debug_toolbar')
        _DT_MW = 'debug_toolbar.middleware.DebugToolbarMiddleware'
        if _DT_MW not in MIDDLEWARE:
            # Toolbar должен идти ПОСЛЕ GZipMiddleware и ПЕРЕД остальными.
            MIDDLEWARE.insert(0, _DT_MW)
        INTERNAL_IPS = ['127.0.0.1']
        DEBUG_TOOLBAR_CONFIG = {
            # Показывать toolbar только при наличии заголовка / куки —
            # удобно при работе через VPN / staging.
            'SHOW_TOOLBAR_CALLBACK': lambda request: DEBUG and request.META.get('REMOTE_ADDR') in INTERNAL_IPS,
        }
    except ImportError:
        # Тулбар не установлен — просто не включаем, без падений.
        pass
