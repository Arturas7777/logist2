"""WebSocket consumers for real-time admin updates."""
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class DataUpdateConsumer(AsyncWebsocketConsumer):
    """Broadcasts admin data updates to authenticated staff users only."""

    GROUP_NAME = "updates"

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not getattr(user, "is_authenticated", False):
            logger.info("WS rejected: anonymous user from %s", self.scope.get("client"))
            await self.close(code=4401)
            return

        if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
            logger.info("WS rejected: non-staff user %s", getattr(user, "username", "?"))
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        await self.send(text_data=json.dumps({"message": "Update received"}))

    async def data_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))

    async def data_update_batch(self, event):
        await self.send(text_data=json.dumps(event.get("data", event)))
