from dotenv import load_dotenv
import os
from pathlib import Path

# Загружаем переменные из .env
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'changeme-in-env')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = str(os.getenv('DEBUG', 'False')).lower() == 'true'

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

# Channels
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

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

# Languages
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
}

# Session settings
SESSION_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = 1209600
SESSION_COOKIE_DOMAIN = None
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_SAVE_EVERY_REQUEST = True

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
            'level': 'DEBUG',
            'propagate': True,
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

# CSRF trusted origins derived from ALLOWED_HOSTS
def _build_csrf_trusted(origins):
    result = []
    for host in origins:
        if host and host != 'localhost' and host != '127.0.0.1':
            result.append(f"https://{host}")
            result.append(f"http://{host}")
    # localhost/http for dev
    result.append('http://localhost')
    result.append('http://127.0.0.1')
    result.append('https://localhost')
    result.append('https://127.0.0.1')
    return result

CSRF_TRUSTED_ORIGINS = _build_csrf_trusted(ALLOWED_HOSTS)

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files (Uploaded content)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'