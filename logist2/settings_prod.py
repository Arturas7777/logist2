from .settings_base import *  # noqa

DEBUG = False

# Harden logging in prod
LOGGING['loggers']['django']['level'] = 'INFO'

# ===================================
# SECURITY SETTINGS
# ===================================

# HTTPS/SSL Security (ВРЕМЕННО ОТКЛЮЧЕНО до настройки домена)
SECURE_SSL_REDIRECT = False  # Redirect all HTTP to HTTPS
SESSION_COOKIE_SECURE = False  # Only send session cookie over HTTPS
CSRF_COOKIE_SECURE = False  # Only send CSRF cookie over HTTPS
SECURE_BROWSER_XSS_FILTER = True  # Enable XSS filter in browser
SECURE_CONTENT_TYPE_NOSNIFF = True  # Prevent MIME type sniffing
X_FRAME_OPTIONS = 'DENY'  # Prevent clickjacking

# HTTP Strict Transport Security (HSTS) - ВРЕМЕННО ОТКЛЮЧЕНО
SECURE_HSTS_SECONDS = 0  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

# Additional Security Headers
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# File Upload Limits
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5 MB

# Session Security
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'

# CSRF Protection
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Strict'
CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'


