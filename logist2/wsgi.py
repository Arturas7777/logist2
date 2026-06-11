"""
WSGI config for logist2 project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

# Local default = dev. Server gunicorn.service must export
# DJANGO_SETTINGS_MODULE=logist2.settings.prod via Environment= directive
# (см. scripts/harden_server.sh и scripts/logist2.service).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "logist2.settings.dev")

application = get_wsgi_application()
