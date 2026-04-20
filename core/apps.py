from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Явный импорт сигналов гарантирует их подключение во всех контекстах
        # (management-команды, Celery worker, тесты), а не только когда
        # Django autodiscover подтянет admin-модули.
        from . import signals  # noqa: F401
