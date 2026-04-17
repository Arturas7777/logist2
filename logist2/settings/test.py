"""Settings for the test suite.

Цели:
- быстрые тесты (SQLite + weak hasher + no migrations);
- изоляция от внешних зависимостей (Redis, email, Celery);
- корректная работа на CI без отдельного Postgres.

Схема создаётся из текущих моделей (а НЕ из миграций) через
`MIGRATION_MODULES = DisableMigrations()`. Это типовая практика
(см. https://docs.djangoproject.com/en/5.1/topics/testing/overview/#the-test-database)
и позволяет обойти легаси-миграции, заточенные под Postgres.
"""

from .base import *  # noqa: F401,F403


class DisableMigrations:
    """Trick pattern: disables all migrations for fast test DB creation."""

    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


DEBUG = False
ALLOWED_HOSTS = ['*']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',  # noqa: F405
    }
}

MIGRATION_MODULES = DisableMigrations()

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Не тянем Sentry в тестах даже если DSN утёк в env.
SENTRY_DSN = ''
