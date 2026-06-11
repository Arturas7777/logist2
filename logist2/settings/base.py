import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "changeme-in-env")

# Отдельный ключ для шифрования банковских и accounting-токенов
# (Revolut/Paysera/site.pro). Если пуст — используется SECRET_KEY как
# fallback (обратная совместимость). См. `core/encryption.py`.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# Comma-separated список старых ключей для расшифровки токенов, ещё не
# пересохранённых новым ключом. Нужен только во время ротации:
# 1) задать новый ENCRYPTION_KEY, старый — в ENCRYPTION_KEY_FALLBACKS;
# 2) запустить `python manage.py rotate_encryption_key`;
# 3) убрать старый ключ из ENCRYPTION_KEY_FALLBACKS.
ENCRYPTION_KEY_FALLBACKS = os.getenv("ENCRYPTION_KEY_FALLBACKS", "")

# Если True — prod-настройки потребуют отдельный ENCRYPTION_KEY (fail-fast).
# Включить ПОСЛЕ того как ENCRYPTION_KEY задан в .env прода и токены
# при необходимости перешифрованы командой rotate_encryption_key.
ENCRYPTION_KEY_REQUIRED = str(os.getenv("ENCRYPTION_KEY_REQUIRED", "False")).lower() == "true"

# Путь к приватному ключу Revolut Business для подписи JWT-assertion.
# Используется в management-команде `regenerate_revolut_jwt` и в admin action
# «Перегенерировать JWT» в BankConnectionAdmin. Дефолт: BASE_DIR/certs/privatecert.pem
# (на сервере это /var/www/.../logist2/certs/privatecert.pem).
# См. docs/CREDENTIALS.md → раздел Revolut Business API.
REVOLUT_PRIVATE_KEY_PATH = os.getenv(
    "REVOLUT_PRIVATE_KEY_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "certs" / "privatecert.pem"),
)

DEBUG = str(os.getenv("DEBUG", "False")).lower() == "true"


def _is_insecure_secret(key: str) -> bool:
    if not key:
        return True
    insecure_markers = ("changeme", "django-insecure", "default", "secret", "test")
    lowered = key.lower()
    for marker in insecure_markers:
        if lowered.startswith(marker) or marker == lowered:
            return True
    if len(key) < 32:
        return True
    return False


# Fail-fast на старте: в проде недопустим дефолтный/слабый SECRET_KEY.
# ENCRYPTION_KEY:
# - по умолчанию — только предупреждение, потому что ранее сохранённые
#   токены Revolut/site.pro могли быть зашифрованы fallback'ом на
#   SECRET_KEY; жёсткий fail-fast сломает их расшифровку;
# - если ENCRYPTION_KEY_REQUIRED=true — обязателен (включается ПОСЛЕ
#   ротации: см. docs/ENCRYPTION_KEY.md).
if not DEBUG:
    if _is_insecure_secret(SECRET_KEY):
        raise ValueError(
            "SECRET_KEY не задан, использует дефолтное значение или слишком короткий. "
            "Установите переменную окружения SECRET_KEY длиной >=32 символа для продакшена."
        )
    if ENCRYPTION_KEY_REQUIRED:
        if not ENCRYPTION_KEY or len(ENCRYPTION_KEY) < 32:
            raise ValueError(
                "ENCRYPTION_KEY_REQUIRED=true, но ENCRYPTION_KEY не задан или короче 32 "
                'символов. Сгенерируйте ключ: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))" и положите в .env. '
                "См. docs/ENCRYPTION_KEY.md по процедуре миграции."
            )
        if ENCRYPTION_KEY == SECRET_KEY:
            raise ValueError(
                "ENCRYPTION_KEY совпадает с SECRET_KEY — отделите ключи, чтобы "
                "компрометация одного не раскрывала банковские токены."
            )
    elif not ENCRYPTION_KEY:
        sys.stderr.write(
            "[logist2] WARNING: ENCRYPTION_KEY не задан — шифрование токенов внешних "
            "API использует SECRET_KEY как fallback. После задания ключа и запуска "
            "`python manage.py rotate_encryption_key` включите "
            "ENCRYPTION_KEY_REQUIRED=true для fail-fast. См. docs/ENCRYPTION_KEY.md.\n"
        )

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

CSRF_TRUSTED_ORIGINS = [
    h.strip() for h in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:8000").split(",") if h.strip()
]

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    "daphne",
    "whitenoise.runserver_nostatic",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "core.apps.CoreConfig",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "logist2.settings_security.SecurityHeadersMiddleware",
    "logist2.settings_security.DebugQueryResetMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "core.middleware_admin_language.AdminRussianLanguageMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # M7: structured logging — request_id / user_id / path в каждой
    # записи лога. Должен идти ПОСЛЕ AuthenticationMiddleware.
    "core.middleware_logging.RequestContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "logist2.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates", BASE_DIR / "core" / "templates"],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
            ],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                ),
            ],
        },
    },
]

WSGI_APPLICATION = "logist2.wsgi.application"
ASGI_APPLICATION = "logist2.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_use_pgbouncer = os.getenv("USE_PGBOUNCER", "false").lower() == "true"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 0 if _use_pgbouncer else 600,
        # Validates connections before using (avoids stale-conn errors).
        "CONN_HEALTH_CHECKS": not _use_pgbouncer,
        "OPTIONS": {
            "connect_timeout": 10,
        },
        "DISABLE_SERVER_SIDE_CURSORS": _use_pgbouncer,
    }
}

if "test" in sys.argv or "pytest" in sys.modules:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",
    }

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

_use_redis_channels = os.getenv("CHANNELS_BACKEND", "memory")

if _use_redis_channels == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [
                    (
                        os.getenv("REDIS_HOST", "127.0.0.1"),
                        int(os.getenv("REDIS_PORT", "6379")),
                    )
                ],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_backend = os.getenv("CACHE_BACKEND", "redis").lower()

if _cache_backend == "redis":
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": (f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/1"),
            "TIMEOUT": 300,
            "KEY_PREFIX": "logist2",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": BASE_DIR / ".cache",
            "TIMEOUT": 300,
        }
    }

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/2"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TIME_LIMIT = 300
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/admin/login/"
LOGOUT_REDIRECT_URL = "/admin/login/"

# ---------------------------------------------------------------------------
# I18n
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ("lt", "Lietuvių"),
    ("en", "English"),
    ("ru", "Русский"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

# ---------------------------------------------------------------------------
# Static / Media
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATICFILES_DIRS = [
    BASE_DIR / "core" / "static",
    BASE_DIR / "static",
]
STATIC_ROOT = os.getenv("STATIC_ROOT", BASE_DIR / "staticfiles")
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = os.getenv("MEDIA_ROOT", BASE_DIR / "media")

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAdminUser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Анонимных запросов на 1 IP/мин: 30 (раньше 60). Большинство
        # анонимных эндпоинтов — track_shipment и публичные API сайта;
        # 30 r/min хватает с запасом и режет ботов.
        "anon": "30/minute",
        "user": "120/minute",
        "track_shipment": "20/minute",
        "ai_chat": "10/minute",
        # Скачивание фото контейнера / объёмных media:
        # 30 запросов/минуту на пользователя — достаточно даже при
        # просмотре карточки контейнера с 50+ фото (берём через REST
        # один раз и кладём в кэш браузера), но защищает от случайного
        # скачивания всех фото в цикле.
        "photo_download": "30/minute",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------
# PDF-инвойсы и архивы фото бывают по 10-20 МБ; дефолт 2.5 МБ слишком мал.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", 26214400))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", 26214400))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FIELDS", 5000))

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_DOMAIN = None
# R1 (AUDIT_ROUND3): cached_db вместо cache — рестарт/флаш Redis больше
# не разлогинивает всех пользователей (сессии персистятся в БД, кэш
# остаётся быстрым слоем чтения). Таблица django_session уже есть
# (django.contrib.sessions в INSTALLED_APPS с первой миграции).
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"
SESSION_SAVE_EVERY_REQUEST = False

# ---------------------------------------------------------------------------
# Logging  (M7 — structured logs + RotatingFileHandler)
# ---------------------------------------------------------------------------
#
# Поддерживаются два формата строки лога: текстовый (`verbose`) и JSON
# (`json`, через python-json-logger). Выбирается env-var `LOG_FORMAT`
# ("verbose" по умолчанию для dev, "json" в проде):
#
#   LOG_FORMAT=json
#   LOG_LEVEL=INFO            # уровень для core/django loggers
#   LOG_DIR=/var/log/logist2  # если задан — добавляется RotatingFileHandler
#                             #   (50 MB × 10 backups → 500 MB max)
#
# Контекст запроса (request_id / user_id / path / method) пристёгивается
# через `core.middleware_logging.RequestContextFilter`. Этот же filter
# работает и вне HTTP-запроса (например в Celery): тогда поля = "-" / None.
# См. docs/LOGGING.md.

LOG_FORMAT = os.getenv("LOG_FORMAT", "verbose").lower()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO" if DEBUG else "WARNING").upper()
LOG_DIR = os.getenv("LOG_DIR", "").strip()

_logging_formatters = {
    "verbose": {
        # Текстовый формат с request_id для удобства tail -f в dev.
        "format": ("{asctime} {levelname} [{request_id}] {name} {message} (user={user_id} {method} {path})"),
        "style": "{",
    },
    "json": {
        # python-json-logger автоматически подхватит дополнительные
        # поля из LogRecord (request_id/user_id/path/method — добавляет
        # RequestContextFilter). Конкретные поля из record указываем
        # в format-строке для гарантированного включения.
        "()": "pythonjsonlogger.json.JsonFormatter",
        "format": ("%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(user_id)s %(path)s %(method)s"),
        "rename_fields": {"levelname": "level", "asctime": "ts", "name": "logger"},
    },
}

_logging_filters = {
    "request_context": {
        "()": "core.middleware_logging.RequestContextFilter",
    },
}

_logging_handlers: dict = {
    "console": {
        "level": "DEBUG",
        "class": "logging.StreamHandler",
        "formatter": LOG_FORMAT if LOG_FORMAT in _logging_formatters else "verbose",
        "filters": ["request_context"],
    },
}

if LOG_DIR:
    # RotatingFileHandler с ротацией по размеру: 50 MB × 10 файлов.
    # Daily-rotation (TimedRotatingFileHandler) намеренно не используем —
    # при гранулярности «один файл = одни сутки» сложно вычислять
    # ретеншн при импорте/всплеске. По размеру предсказуемее.
    _logging_handlers["file"] = {
        "level": LOG_LEVEL,
        "class": "logging.handlers.RotatingFileHandler",
        "filename": os.path.join(LOG_DIR, "app.log"),
        "maxBytes": int(os.getenv("LOG_MAX_BYTES", str(50 * 1024 * 1024))),
        "backupCount": int(os.getenv("LOG_BACKUP_COUNT", "10")),
        "formatter": "json",
        "filters": ["request_context"],
        "encoding": "utf-8",
    }

_loggers_handlers = ["console"] + (["file"] if LOG_DIR else [])

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": _logging_formatters,
    "filters": _logging_filters,
    "handlers": _logging_handlers,
    "loggers": {
        "django": {
            "handlers": _loggers_handlers,
            "level": "WARNING",
            "propagate": True,
        },
        "core": {
            "handlers": _loggers_handlers,
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# WhiteNoise
# ---------------------------------------------------------------------------

WHITENOISE_MIMETYPES = {
    ".js": "application/javascript",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"


def _build_csrf_trusted(env_origins, hosts, *, allow_http=DEBUG):
    result = list(env_origins)
    schemes = ("https", "http") if allow_http else ("https",)
    for host in hosts:
        if host and host not in ("localhost", "127.0.0.1"):
            for scheme in schemes:
                origin = f"{scheme}://{host}"
                if origin not in result:
                    result.append(origin)
    for fallback in (
        "http://localhost",
        "http://127.0.0.1",
        "https://localhost",
        "https://127.0.0.1",
    ):
        if fallback not in result:
            result.append(fallback)
    return result


CSRF_TRUSTED_ORIGINS = _build_csrf_trusted(CSRF_TRUSTED_ORIGINS, ALLOWED_HOSTS)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USE_TLS = str(os.getenv("EMAIL_USE_TLS", "True")).lower() == "true"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Caromoto Lithuania <noreply@caromoto-lt.com>")

# ---------------------------------------------------------------------------
# Telegram — уведомления клиентам о разгрузке (дублируют email-канал)
# ---------------------------------------------------------------------------
# Токен бота из @BotFather. Если пусто — Telegram-уведомления отключены,
# работает только email-канал.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# Username бота (без @) — нужен для персональных deep-link приглашений
# вида https://t.me/<username>?start=<token>.
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "CaromotoLT_bot").strip().lstrip("@")
# Глобальный флаг (по умолчанию включён, если задан токен). Позволяет
# выключить рассылку, не удаляя токен.
TELEGRAM_NOTIFICATIONS_ENABLED = str(os.getenv("TELEGRAM_NOTIFICATIONS_ENABLED", "True")).lower() == "true" and bool(
    TELEGRAM_BOT_TOKEN
)
# Таймаут HTTP-запросов к Telegram Bot API, сек.
TELEGRAM_API_TIMEOUT = int(os.getenv("TELEGRAM_API_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

AI_CHAT_ENABLED = os.getenv("AI_CHAT_ENABLED", "False").lower() == "true"
AI_API_KEY = os.getenv("AI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
AI_API_BASE_URL = os.getenv("AI_API_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "400"))
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.2"))
AI_REQUEST_TIMEOUT = int(os.getenv("AI_REQUEST_TIMEOUT", "40"))
AI_EMBEDDINGS_MODEL = os.getenv("AI_EMBEDDINGS_MODEL", "text-embedding-3-small")
AI_RAG_INDEX_PATH = os.getenv("AI_RAG_INDEX_PATH", os.path.join(BASE_DIR, "data", "ai_rag_index.json"))
AI_RAG_TOP_K = int(os.getenv("AI_RAG_TOP_K", "4"))
AI_RAG_MAX_AGE_HOURS = int(os.getenv("AI_RAG_MAX_AGE_HOURS", "24"))

# ---------------------------------------------------------------------------
# Gmail OAuth — переписка по контейнерам (Phase 1: read-only)
# ---------------------------------------------------------------------------

GMAIL_ENABLED = str(os.getenv("GMAIL_ENABLED", "False")).lower() == "true"
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID", "").strip()
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN", "").strip()
GMAIL_USER_EMAIL = os.getenv("GMAIL_USER_EMAIL", "").strip()
GMAIL_INITIAL_LOOKBACK_DAYS = int(os.getenv("GMAIL_INITIAL_LOOKBACK_DAYS", "30"))
GMAIL_MAX_ATTACHMENT_MB = int(os.getenv("GMAIL_MAX_ATTACHMENT_MB", "25"))
GMAIL_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Scopes: чтение + отправка (Phase 2).
# При изменении — перегенерировать refresh_token через
# scripts/get_gmail_refresh_token.py.
GMAIL_SCOPES = [
    # gmail.modify = readonly + менять лейблы (нужно для синхронизации UNREAD
    # при пометке писем прочитанными в карточке контейнера).
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

# ─── Phase 2: параметры исходящих писем ────────────────────────────────────
# Имя и email отправителя. Если GMAIL_FROM_EMAIL пустой — используется
# authenticated аккаунт (GMAIL_USER_EMAIL). Чтобы слать от имени alias — alias
# должен быть настроен в Gmail → Settings → Accounts → Send mail as.
GMAIL_FROM_NAME = os.getenv("GMAIL_FROM_NAME", "Caromoto Lithuania").strip()
GMAIL_FROM_EMAIL = os.getenv("GMAIL_FROM_EMAIL", "").strip()
# Подпись добавляется в конец исходящих писем (plain text + html).
GMAIL_SIGNATURE_TEXT = os.getenv(
    "GMAIL_SIGNATURE_TEXT",
    "--\nCaromoto Lithuania\nhttps://caromoto-lt.com",
)
GMAIL_SIGNATURE_HTML = os.getenv(
    "GMAIL_SIGNATURE_HTML",
    '<p style="color:#6b7280;font-size:13px;">--<br>'
    "Caromoto Lithuania<br>"
    '<a href="https://caromoto-lt.com">caromoto-lt.com</a></p>',
)
# Максимальный суммарный размер вложений исходящего письма, МБ.
# Gmail принимает до 35 МБ raw ≈ 25 МБ после base64.
GMAIL_MAX_OUTBOUND_MB = int(os.getenv("GMAIL_MAX_OUTBOUND_MB", "25"))

# ---------------------------------------------------------------------------
# Google Drive API — синхронизация фотографий контейнеров
# ---------------------------------------------------------------------------
# Публичный API key (НЕ OAuth). Используется в core/services/gdrive_client.py
# для чтения списков файлов и скачивания. Нужен, потому что HTML-парсинг
# embeddedfolderview/обычной страницы папки режет большие папки до ~50-120
# элементов (Drive догружает остальное JavaScript-ом). API v3 отдаёт всё с
# полной пагинацией.
#
# Как получить:
#   1. Google Cloud Console → выбрать проект.
#   2. APIs & Services → Library → включить "Google Drive API".
#   3. APIs & Services → Credentials → Create Credentials → API key.
#   4. Обязательно: Restrict key → API restrictions → Drive API only.
#
# Папки Drive должны быть опубликованы как "Anyone with the link" (сейчас
# именно так — другой вариант потребовал бы OAuth с drive.readonly scope).
# Если ключ не задан — код откатывается на старый HTML-парсинг с warning-ом.
GOOGLE_DRIVE_API_KEY = os.getenv("GOOGLE_DRIVE_API_KEY", "").strip()

# ---------------------------------------------------------------------------
# Company info (used in email templates)
# ---------------------------------------------------------------------------

COMPANY_NAME = "Caromoto Lithuania, MB"
COMPANY_PHONE = "+37068830450"
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "info@caromoto-lt.com")
COMPANY_WEBSITE = "https://caromoto-lt.com"

# ---------------------------------------------------------------------------
# System monitoring (/admin/system-monitor/) — psutil + celery beat
# ---------------------------------------------------------------------------
# Сколько дней хранить SystemMetric/UptimeCheck. По дефолту 30 дней
# (~8 600 + 43 200 строк = ≈10 MB на postgres).
MONITORING_RETENTION_DAYS = int(os.getenv("MONITORING_RETENTION_DAYS", "30"))
# URL для ping_uptime task. Локально — gunicorn/runserver, на сервере —
# nginx upstream. По дефолту локальный health endpoint.
MONITORING_HEALTH_URL = os.getenv("MONITORING_HEALTH_URL", "http://127.0.0.1:8000/health/")

# ---------------------------------------------------------------------------
# Sentry (error monitoring) — optional, enabled only when SENTRY_DSN is set
# ---------------------------------------------------------------------------

SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "development" if DEBUG else "production")
SENTRY_RELEASE = os.getenv("SENTRY_RELEASE", "")
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
SENTRY_PROFILES_SAMPLE_RATE = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))
SENTRY_SEND_PII = str(os.getenv("SENTRY_SEND_PII", "False")).lower() == "true"

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        # M7: фильтруем поток в Sentry — теперь file-handler ловит INFO+,
        # а в Sentry уходят только ERROR+ events (breadcrumbs остаются по
        # умолчанию = INFO+ для контекста ошибки).
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            integrations=[
                DjangoIntegration(
                    transaction_style="url",
                    middleware_spans=False,
                    signals_spans=False,
                ),
                CeleryIntegration(monitor_beat_tasks=True),
                RedisIntegration(),
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                ),
            ],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
            send_default_pii=SENTRY_SEND_PII,
            attach_stacktrace=True,
            max_breadcrumbs=50,
        )
        sentry_sdk.set_tag("service", "logist2")
    except ImportError:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. Run: pip install 'sentry-sdk[django,celery]'"
        )
