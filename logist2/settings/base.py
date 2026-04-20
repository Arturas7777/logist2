import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-in-env')

ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')

DEBUG = str(os.getenv('DEBUG', 'False')).lower() == 'true'


def _is_insecure_secret(key: str) -> bool:
    if not key:
        return True
    insecure_markers = ('changeme', 'django-insecure', 'default', 'secret', 'test')
    lowered = key.lower()
    for marker in insecure_markers:
        if lowered.startswith(marker) or marker == lowered:
            return True
    if len(key) < 32:
        return True
    return False


# Fail-fast на старте: в проде / при запуске воркера недопустимо
# использовать дефолтный/слабый SECRET_KEY или пустой ENCRYPTION_KEY
# (от него зависит шифрование токенов Revolut и Google Drive).
if not DEBUG:
    if _is_insecure_secret(SECRET_KEY):
        raise ValueError(
            "SECRET_KEY не задан, использует дефолтное значение или слишком короткий. "
            "Установите переменную окружения SECRET_KEY длиной ≥32 символа для продакшена."
        )
    if not ENCRYPTION_KEY:
        raise ValueError(
            "ENCRYPTION_KEY не задан. Он нужен для шифрования токенов внешних API "
            "(Revolut, Google). Сгенерируйте: `python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\"` и положите в .env."
        )

ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
    if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    h.strip()
    for h in os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:8000').split(',')
    if h.strip()
]

# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'daphne',
    'whitenoise.runserver_nostatic',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'rest_framework',
    'core.apps.CoreConfig',
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'logist2.settings_security.SecurityHeadersMiddleware',
    'logist2.settings_security.DebugQueryResetMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'core.middleware_admin_language.AdminRussianLanguageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'logist2.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates', BASE_DIR / 'core' / 'templates'],
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
            ],
            'loaders': [
                ('django.template.loaders.cached.Loader', [
                    'django.template.loaders.filesystem.Loader',
                    'django.template.loaders.app_directories.Loader',
                ]),
            ],
        },
    },
]

WSGI_APPLICATION = 'logist2.wsgi.application'
ASGI_APPLICATION = 'logist2.asgi.application'

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_use_pgbouncer = os.getenv('USE_PGBOUNCER', 'false').lower() == 'true'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'CONN_MAX_AGE': 0 if _use_pgbouncer else 600,
        # Validates connections before using (avoids stale-conn errors).
        'CONN_HEALTH_CHECKS': not _use_pgbouncer,
        'OPTIONS': {
            'connect_timeout': 10,
        },
        'DISABLE_SERVER_SIDE_CURSORS': _use_pgbouncer,
    }
}

if 'test' in sys.argv or 'pytest' in sys.modules:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    }

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------

_use_redis_channels = os.getenv('CHANNELS_BACKEND', 'memory')

if _use_redis_channels == 'redis':
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [(
                    os.getenv('REDIS_HOST', '127.0.0.1'),
                    int(os.getenv('REDIS_PORT', '6379')),
                )],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_backend = os.getenv('CACHE_BACKEND', 'redis').lower()

if _cache_backend == 'redis':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': (
                f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}"
                f":{os.getenv('REDIS_PORT', '6379')}/1"
            ),
            'TIMEOUT': 300,
            'KEY_PREFIX': 'logist2',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
            'LOCATION': BASE_DIR / '.cache',
            'TIMEOUT': 300,
        }
    }

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = (
    f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}"
    f":{os.getenv('REDIS_PORT', '6379')}/2"
)
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TIME_LIMIT = 300
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/admin/login/'
LOGOUT_REDIRECT_URL = '/admin/login/'

# ---------------------------------------------------------------------------
# I18n
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'ru'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

LANGUAGES = [
    ('lt', 'Lietuvių'),
    ('en', 'English'),
    ('ru', 'Русский'),
]

LOCALE_PATHS = [BASE_DIR / 'locale']

# ---------------------------------------------------------------------------
# Static / Media
# ---------------------------------------------------------------------------

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'core' / 'static',
    BASE_DIR / 'static',
]
STATIC_ROOT = os.getenv('STATIC_ROOT', BASE_DIR / 'staticfiles')
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', BASE_DIR / 'media')

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAdminUser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '120/minute',
        'track_shipment': '20/minute',
        'ai_chat': '10/minute',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------
# PDF-инвойсы и архивы фото бывают по 10-20 МБ; дефолт 2.5 МБ слишком мал.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', 26214400))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', 26214400))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.getenv('DATA_UPLOAD_MAX_NUMBER_FIELDS', 5000))

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_DOMAIN = None
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'
SESSION_SAVE_EVERY_REQUEST = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': True,
        },
        'core': {
            'handlers': ['console'],
            'level': 'INFO' if DEBUG else 'WARNING',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------------------
# WhiteNoise
# ---------------------------------------------------------------------------

WHITENOISE_MIMETYPES = {
    '.js': 'application/javascript',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'


def _build_csrf_trusted(env_origins, hosts):
    result = list(env_origins)
    for host in hosts:
        if host and host not in ('localhost', '127.0.0.1'):
            for scheme in ('https', 'http'):
                origin = f"{scheme}://{host}"
                if origin not in result:
                    result.append(origin)
    for fallback in (
        'http://localhost', 'http://127.0.0.1',
        'https://localhost', 'https://127.0.0.1',
    ):
        if fallback not in result:
            result.append(fallback)
    return result


CSRF_TRUSTED_ORIGINS = _build_csrf_trusted(CSRF_TRUSTED_ORIGINS, ALLOWED_HOSTS)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = str(os.getenv('EMAIL_USE_TLS', 'True')).lower() == 'true'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL', 'Caromoto Lithuania <noreply@caromoto-lt.com>'
)

# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------

AI_CHAT_ENABLED = os.getenv('AI_CHAT_ENABLED', 'False').lower() == 'true'
AI_API_KEY = os.getenv('AI_API_KEY', os.getenv('OPENAI_API_KEY', ''))
AI_API_BASE_URL = os.getenv('AI_API_BASE_URL', 'https://api.openai.com/v1')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '400'))
AI_TEMPERATURE = float(os.getenv('AI_TEMPERATURE', '0.2'))
AI_REQUEST_TIMEOUT = int(os.getenv('AI_REQUEST_TIMEOUT', '40'))
AI_EMBEDDINGS_MODEL = os.getenv('AI_EMBEDDINGS_MODEL', 'text-embedding-3-small')
AI_RAG_INDEX_PATH = os.getenv(
    'AI_RAG_INDEX_PATH', os.path.join(BASE_DIR, 'data', 'ai_rag_index.json')
)
AI_RAG_TOP_K = int(os.getenv('AI_RAG_TOP_K', '4'))
AI_RAG_MAX_AGE_HOURS = int(os.getenv('AI_RAG_MAX_AGE_HOURS', '24'))

# ---------------------------------------------------------------------------
# Gmail OAuth — переписка по контейнерам (Phase 1: read-only)
# ---------------------------------------------------------------------------

GMAIL_ENABLED = str(os.getenv('GMAIL_ENABLED', 'False')).lower() == 'true'
GMAIL_CLIENT_ID = os.getenv('GMAIL_CLIENT_ID', '').strip()
GMAIL_CLIENT_SECRET = os.getenv('GMAIL_CLIENT_SECRET', '').strip()
GMAIL_REFRESH_TOKEN = os.getenv('GMAIL_REFRESH_TOKEN', '').strip()
GMAIL_USER_EMAIL = os.getenv('GMAIL_USER_EMAIL', '').strip()
GMAIL_INITIAL_LOOKBACK_DAYS = int(os.getenv('GMAIL_INITIAL_LOOKBACK_DAYS', '30'))
GMAIL_MAX_ATTACHMENT_MB = int(os.getenv('GMAIL_MAX_ATTACHMENT_MB', '25'))
GMAIL_TOKEN_URI = 'https://oauth2.googleapis.com/token'
# Scopes: чтение + отправка (Phase 2).
# При изменении — перегенерировать refresh_token через
# scripts/get_gmail_refresh_token.py.
GMAIL_SCOPES = [
    # gmail.modify = readonly + менять лейблы (нужно для синхронизации UNREAD
    # при пометке писем прочитанными в карточке контейнера).
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
]

# ─── Phase 2: параметры исходящих писем ────────────────────────────────────
# Имя и email отправителя. Если GMAIL_FROM_EMAIL пустой — используется
# authenticated аккаунт (GMAIL_USER_EMAIL). Чтобы слать от имени alias — alias
# должен быть настроен в Gmail → Settings → Accounts → Send mail as.
GMAIL_FROM_NAME = os.getenv('GMAIL_FROM_NAME', 'Caromoto Lithuania').strip()
GMAIL_FROM_EMAIL = os.getenv('GMAIL_FROM_EMAIL', '').strip()
# Подпись добавляется в конец исходящих писем (plain text + html).
GMAIL_SIGNATURE_TEXT = os.getenv(
    'GMAIL_SIGNATURE_TEXT',
    '--\nCaromoto Lithuania\nhttps://caromoto-lt.com',
)
GMAIL_SIGNATURE_HTML = os.getenv(
    'GMAIL_SIGNATURE_HTML',
    '<p style="color:#6b7280;font-size:13px;">--<br>'
    'Caromoto Lithuania<br>'
    '<a href="https://caromoto-lt.com">caromoto-lt.com</a></p>',
)
# Максимальный суммарный размер вложений исходящего письма, МБ.
# Gmail принимает до 35 МБ raw ≈ 25 МБ после base64.
GMAIL_MAX_OUTBOUND_MB = int(os.getenv('GMAIL_MAX_OUTBOUND_MB', '25'))

# ---------------------------------------------------------------------------
# Company info (used in email templates)
# ---------------------------------------------------------------------------

COMPANY_NAME = 'Caromoto Lithuania'
COMPANY_PHONE = '+37068830450'
COMPANY_EMAIL = os.getenv('COMPANY_EMAIL', 'info@caromoto-lt.com')
COMPANY_WEBSITE = 'https://caromoto-lt.com'

# ---------------------------------------------------------------------------
# Sentry (error monitoring) — optional, enabled only when SENTRY_DSN is set
# ---------------------------------------------------------------------------

SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
SENTRY_ENVIRONMENT = os.getenv('SENTRY_ENVIRONMENT', 'development' if DEBUG else 'production')
SENTRY_RELEASE = os.getenv('SENTRY_RELEASE', '')
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.05'))
SENTRY_PROFILES_SAMPLE_RATE = float(os.getenv('SENTRY_PROFILES_SAMPLE_RATE', '0.0'))
SENTRY_SEND_PII = str(os.getenv('SENTRY_SEND_PII', 'False')).lower() == 'true'

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            integrations=[
                DjangoIntegration(
                    transaction_style='url',
                    middleware_spans=False,
                    signals_spans=False,
                ),
                CeleryIntegration(monitor_beat_tasks=True),
                RedisIntegration(),
                LoggingIntegration(level=None, event_level=None),
            ],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
            send_default_pii=SENTRY_SEND_PII,
            attach_stacktrace=True,
            max_breadcrumbs=50,
        )
        sentry_sdk.set_tag('service', 'logist2')
    except ImportError:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but sentry-sdk is not installed. Run: pip install 'sentry-sdk[django,celery]'"
        )
