from .settings import *  # noqa
from pathlib import Path

# Use SQLite for tests to avoid CREATEDB permission issues on Postgres
BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = True

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test.sqlite3',
    }
}

# In-memory Channels layer for tests (no Redis needed)
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Faster password hashing in tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Relax security for tests
SECURE_SSL_REDIRECT = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False

# Quieter logging for faster tests
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'null': {'class': 'logging.NullHandler'},
    },
    'loggers': {
        'django': {
            'handlers': ['null'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Optional: speed up by bypassing migrations for core app
MIGRATION_MODULES = {
    'core': None,
}


