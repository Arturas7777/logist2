"""
ASGI config for logist2 project.
"""
import os

# Must be set BEFORE importing anything that touches Django settings.
# Local default = dev. Server daphne.service exports
# DJANGO_SETTINGS_MODULE=logist2.settings.prod (см. scripts/daphne.service).
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings.dev')

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

import core.routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            core.routing.websocket_urlpatterns
        )
    ),
})
