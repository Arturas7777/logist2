from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '*']

LOGGING['loggers']['django']['level'] = 'DEBUG'
LOGGING['loggers']['core']['level'] = 'DEBUG'

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
