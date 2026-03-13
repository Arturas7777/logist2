from dotenv import load_dotenv
import os
from pathlib import Path

# Загружаем переменные из .env
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-in-env')

# Отдельный ключ для шифрования данных в БД (Fernet).
# Если не задан, используется SECRET_KEY (обратная совместимость).
# ВАЖНО: после установки ENCRYPTION_KEY не меняйте его — иначе зашифрованные
# банковские токены в БД станут нечитаемыми.
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = str(os.getenv('DEBUG', 'False')).lower() == 'true'

# Проверка SECRET_KEY в продакшене
if not DEBUG and SECRET_KEY in ('changeme-in-env', '', 'django-insecure'):
    raise ValueError(
        "SECRET_KEY не задан или использует дефолтное значение! "
        "Установите переменную окружения SECRET_KEY для продакшена."
    )

# ALLOWED_HOSTS из .env, разделённые запятыми
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

# CSRF trusted origins для продакшн
CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.getenv('CSRF_TRUSTED_ORIGINS', 'http://localhost:8000').split(',') if h.strip()]

# Application definition
INSTALLED_APPS = [
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
    'logist2.settings_security.DebugQueryResetMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
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
        'DIRS': [BASE_DIR / 'templates', BASE_DIR / 'core/templates'],
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
# ASGI_APPLICATION = 'logist2.asgi.application'  # Отключено для in-memory channels

# Database with connection pooling for better performance
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
        # Connection pooling - переиспользование соединений
        'CONN_MAX_AGE': 600,  # 10 минут
        'OPTIONS': {
            'connect_timeout': 10,
        }
    }
}

# Для тестов используем SQLite (быстрее, не требует PostgreSQL)
import sys
if 'test' in sys.argv or 'pytest' in sys.modules:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
    }

# Channels
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Cache — Redis (как на VPS), с fallback на FileBasedCache если Redis недоступен
def _get_cache_config():
    """Пробуем подключиться к Redis; если недоступен — файловый кэш."""
    redis_url = f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/1"
    try:
        import socket
        host = os.getenv('REDIS_HOST', '127.0.0.1')
        port = int(os.getenv('REDIS_PORT', '6379'))
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        return {
            'default': {
                'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                'LOCATION': redis_url,
                'TIMEOUT': 300,
                'KEY_PREFIX': 'logist2',
            }
        }
    except (ConnectionRefusedError, OSError, socket.timeout):
        return {
            'default': {
                'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
                'LOCATION': BASE_DIR / '.cache',
                'TIMEOUT': 300,
            }
        }

CACHES = _get_cache_config()

# Celery — Redis db=2 (same layout as production)
CELERY_BROKER_URL = f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/2"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# Password validation
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

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / "core" / "static",
    BASE_DIR / "static",
]
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Media files (Uploaded content)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# DRF
REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAdminUser',
    ],
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

# Session settings
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_DOMAIN = None
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'
SESSION_SAVE_EVERY_REQUEST = False

# Logging
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
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
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# WhiteNoise MIME types
WHITENOISE_MIMETYPES = {
    '.js': 'application/javascript',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
}

# Security settings for production
SECURE_SSL_REDIRECT = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# Additional security headers
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

def _build_csrf_trusted(env_origins, hosts):
    """Combine explicit env origins with auto-generated ones from ALLOWED_HOSTS."""
    result = list(env_origins)
    for host in hosts:
        if host and host != 'localhost' and host != '127.0.0.1':
            for scheme in ('https', 'http'):
                origin = f"{scheme}://{host}"
                if origin not in result:
                    result.append(origin)
    for fallback in ('http://localhost', 'http://127.0.0.1', 'https://localhost', 'https://127.0.0.1'):
        if fallback not in result:
            result.append(fallback)
    return result

CSRF_TRUSTED_ORIGINS = _build_csrf_trusted(CSRF_TRUSTED_ORIGINS, ALLOWED_HOSTS)

# Email settings for notifications
# Для тестирования используем console backend (письма выводятся в терминал)
# На production установите EMAIL_BACKEND в .env
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_USE_TLS = str(os.getenv('EMAIL_USE_TLS', 'True')).lower() == 'true'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Caromoto Lithuania <noreply@caromoto-lt.com>')

# AI Chat settings
AI_CHAT_ENABLED = os.getenv('AI_CHAT_ENABLED', 'False').lower() == 'true'
AI_API_KEY = os.getenv('AI_API_KEY', os.getenv('OPENAI_API_KEY', ''))
AI_API_BASE_URL = os.getenv('AI_API_BASE_URL', 'https://api.openai.com/v1')
AI_MODEL = os.getenv('AI_MODEL', 'gpt-4o-mini')
AI_MAX_TOKENS = int(os.getenv('AI_MAX_TOKENS', '400'))
AI_TEMPERATURE = float(os.getenv('AI_TEMPERATURE', '0.2'))
AI_REQUEST_TIMEOUT = int(os.getenv('AI_REQUEST_TIMEOUT', '40'))

# Company info for email templates
COMPANY_NAME = 'Caromoto Lithuania'
COMPANY_PHONE = '+37068830450'
COMPANY_EMAIL = 'info@caromoto-lt.com'
COMPANY_WEBSITE = 'https://caromoto-lt.com'