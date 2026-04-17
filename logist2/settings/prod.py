from .base import *  # noqa: F401,F403

DEBUG = False

LOGGING['loggers']['django']['level'] = 'INFO'

# HTTPS
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# HSTS
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# Upload limits — наследуются из base.py (26 MB). Переопределить через env если нужно.

# Session hardening
SESSION_COOKIE_AGE = 86400
# SESSION_SAVE_EVERY_REQUEST=True создавало лишнюю нагрузку на Redis.
# Сессия обновляется на логине/логауте, чего достаточно.
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SAMESITE = 'Strict'

# CSRF hardening
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Strict'
CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'

# Email: use real SMTP in production
EMAIL_BACKEND = os.getenv(  # noqa: F405
    'EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend'
)
