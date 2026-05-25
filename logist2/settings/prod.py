from .base import *

DEBUG = False

LOGGING["loggers"]["django"]["level"] = "INFO"
# Явно поднимаем core до INFO, чтобы в проде не попадал DEBUG-шум
# (logger.debug в core/* периодически зовётся при пересчёте цен и пр.).
if "core" in LOGGING["loggers"]:
    LOGGING["loggers"]["core"]["level"] = "INFO"
else:
    LOGGING["loggers"]["core"] = {"handlers": ["console"], "level": "INFO", "propagate": False}

# HTTPS
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# HSTS
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Upload limits — наследуются из base.py (26 MB). Переопределить через env если нужно.

# Session hardening
SESSION_COOKIE_AGE = 86400
# SESSION_SAVE_EVERY_REQUEST=True создавало лишнюю нагрузку на Redis.
# Сессия обновляется на логине/логауте, чего достаточно.
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SAMESITE = "Strict"

# CSRF hardening
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_FAILURE_VIEW = "django.views.csrf.csrf_failure"

# Email: use real SMTP in production
EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)

# Жёсткий guard: ConsoleEmailBackend в проде = тихая потеря писем.
# Если кто-то по ошибке выставит EMAIL_BACKEND=console.EmailBackend в
# прод-окружении — упадём при импорте settings, а не при первой
# попытке отправки уведомления, которая теряется в логах.
if "console" in (EMAIL_BACKEND or "").lower():
    raise RuntimeError(
        f"Refusing to start with EMAIL_BACKEND={EMAIL_BACKEND!r} in prod settings. "
        "Console email backend would silently swallow real notifications. "
        "Set EMAIL_BACKEND env var to a real SMTP backend."
    )
