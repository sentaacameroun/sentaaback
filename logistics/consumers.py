from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Q

from logistics.models import Delivery


class DeliveryTrackingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.delivery_id = self.scope["url_route"]["kwargs"]["delivery_id"]
        user = self.scope["user"]

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self._can_view(user):
            await self.close(code=4003)
            return

        self.group_name = f"delivery_{self.delivery_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def location_update(self, event):
        await self.send_json(event["location"])

    @database_sync_to_async
    def _can_view(self, user):
        return Delivery.objects.filter(
            Q(id=self.delivery_id)
            & (Q(courier=user) | Q(order__buyer=user) | Q(order__listing__seller=user))
        ).exists()


class CourierDispatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user or not user.is_authenticated or not user.is_courier:
            await self.close(code=4003)
            return

        self.group_name = f"courier_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def delivery_available(self, event):
        await self.send_json(event["delivery"])
