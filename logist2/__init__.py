try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except ImportError:
    celery_app = None
    __all__ = ()

# Подключаем кастомный AdminSite с группировкой меню
from .admin_site import admin_site  # noqa: E402
from django.contrib import admin    # noqa: E402
admin.site = admin_site
# Обязательно обновляем default_site, чтобы autodiscover подхватил наш site
admin.sites.site = admin_site
