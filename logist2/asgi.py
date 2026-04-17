"""
ASGI config for logist2 project.
"""
import os

# Must be set BEFORE importing anything that touches Django settings.
# Fallback only: on the server systemd unit (daphne.service) exports
# DJANGO_SETTINGS_MODULE=logist2.settings.prod.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'logist2.settings')

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from django.core.asgi import get_asgi_application  # noqa: E402

import core.routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            core.routing.websocket_urlpatterns
        )
    ),
})
