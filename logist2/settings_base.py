from dotenv import load_dotenv
import os
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-in-env')
DEBUG = str(os.getenv('DEBUG', 'False')).lower() == 'true'

# Проверка SECRET_KEY в продакшене
if not DEBUG and SECRET_KEY in ('changeme-in-env', '', 'django-insecure'):
    raise ValueError(
        "SECRET_KEY не задан или использует дефолтное значение! "
        "Установите переменную окружения SECRET_KEY для продакшена."
    )
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

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

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'logist2.settings_security.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'core.middleware_admin_language.AdminRussianLanguageMiddleware',  # Принудительно русский для админки
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
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',
            ],
        },
    },
]

WSGI_APPLICATION = 'logist2.wsgi.application'
ASGI_APPLICATION = 'logist2.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
        'CONN_MAX_AGE': 600,
        'OPTIONS': {
            'connect_timeout': 10,
        },
    }
}

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [(os.getenv('REDIS_HOST', '127.0.0.1'), int(os.getenv('REDIS_PORT', '6379')))],
        },
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
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

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'core' / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAdminUser'],
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

# Cache — Redis db=1 (Channels uses db=0)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/1",
        'TIMEOUT': 300,
        'KEY_PREFIX': 'logist2',
    }
}

# Celery — Redis db=2 (db=0 Channels, db=1 Cache)
CELERY_BROKER_URL = f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/2"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_TASK_TIME_LIMIT = 300
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_DOMAIN = None
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_SAVE_EVERY_REQUEST = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'level': 'DEBUG', 'class': 'logging.StreamHandler'},
    },
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'DEBUG', 'propagate': True},
    },
}

WHITENOISE_MIMETYPES = {
    '.js': 'application/javascript',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}

SECURE_SSL_REDIRECT = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

# Email settings for notifications
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '25'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'False').lower() == 'true'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@caromoto-lt.com')

# AI Chat settings
AI_CHAT_ENABLED = os.getenv('AI_CHAT_ENABLED', 'False').lower() == 'true'
AI_API_KEY = os.getenv('AI_API_KEY', os.getenv('OPENAI_API_KEY', ''))
AI_API_BASE_URL = os.getenv('AI_API_BASE_URL', 'https://api.openai.com/v1')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '400'))
AI_TEMPERATURE = float(os.getenv('AI_TEMPERATURE', '0.2'))
AI_REQUEST_TIMEOUT = int(os.getenv('AI_REQUEST_TIMEOUT', '40'))
AI_EMBEDDINGS_MODEL = os.getenv('AI_EMBEDDINGS_MODEL', 'text-embedding-3-small')
AI_RAG_INDEX_PATH = os.getenv('AI_RAG_INDEX_PATH', os.path.join(BASE_DIR, 'core', 'ai_rag_index.json'))
AI_RAG_TOP_K = int(os.getenv('AI_RAG_TOP_K', '4'))
AI_RAG_MAX_AGE_HOURS = int(os.getenv('AI_RAG_MAX_AGE_HOURS', '24'))

# Company info for email templates
COMPANY_NAME = 'Caromoto Lithuania'
COMPANY_PHONE = '+37068830450'
COMPANY_EMAIL = 'lithuania@caromoto.com'
COMPANY_WEBSITE = 'https://caromoto-lt.com'

def _build_csrf_trusted(origins):
    result = []
    for host in origins:
        if host and host not in ('localhost', '127.0.0.1'):
            result.append(f'https://{host}')
            result.append(f'http://{host}')
    result += ['http://localhost', 'http://127.0.0.1', 'https://localhost', 'https://127.0.0.1']
    return result

CSRF_TRUSTED_ORIGINS = _build_csrf_trusted(ALLOWED_HOSTS)


