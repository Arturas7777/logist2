"""Test settings WITH migrations enabled (для M4 — periodic CI job).

В отличие от `logist2.settings.test`, этот профиль:

- не отключает миграции (`MIGRATION_MODULES` не переопределяется),
  чтобы тесты прокатывали ВСЮ цепочку из `core/migrations/0001..*` →
  ловят расхождения миграций и реальной схемы;
- использует PostgreSQL (берёт креды из env-переменных `DB_*`, как
  prod / CI), т.к. много миграций PG-специфичны (BTREE-партиции,
  UUID-функции, JSONB-индексы, `varchar_pattern_ops`).

Запуск:

    DJANGO_SETTINGS_MODULE=logist2.settings.test_migrations \\
        pytest --tb=short --maxfail=5

Используется только в job `tests-with-migrations` в
`.github/workflows/ci.yml` (расписание + PR-label
`run-migrations-ci`). Локально для повседневной разработки
продолжайте использовать `logist2.settings.test` (~3 сек на SQLite).
"""

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["*"]

# base.py вычисляет SECURE_SSL_REDIRECT из env DEBUG; на CI env DEBUG=False,
# и тестовый клиент ловит 301 на http-запросах. В тестах редирект не нужен.
SECURE_SSL_REDIRECT = False

# На CI collectstatic не запускается, поэтому manifest-storage WhiteNoise
# падает на {% static %} при рендеринге шаблонов («Missing staticfiles
# manifest entry»). В тестах хеширование статики не нужно.
STORAGES = {
    **STORAGES,
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# БД — PostgreSQL из env-переменных DB_* (как prod / CI). На CI:
#   DB_NAME=test_logist2 DB_USER=test_user DB_PASSWORD=test_pass
#   DB_HOST=localhost DB_PORT=5432
# (см. github workflow service postgres).
# Локально, если кто-то решит запустить с миграциями: должен поднять
# свой Postgres с такими же кредами или переопределить env.
#
# ВАЖНО: base.py при запуске под pytest подменяет DATABASES на SQLite
# (быстрый дефолт для logist2.settings.test). Для этого профиля возвращаем
# PostgreSQL явно — иначе «тесты с миграциями» молча бегут на SQLite.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# Миграции НЕ отключаем (это смысл этого профиля). Если в base.py
# вдруг появится MIGRATION_MODULES — здесь его не трогаем.

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# MD5 — заметно быстрее на больших тестах с createsuperuser.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Sentry-отчёты из CI не нужны.
SENTRY_DSN = ""
