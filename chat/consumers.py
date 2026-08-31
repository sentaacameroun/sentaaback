from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db import transaction

from chat.models import Conversation
from chat.models import Message
from notifications.tasks import send_push_notification_task


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        user = self.scope["user"]

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self._is_participant(user):
            await self.close(code=4003)
            return

        self.group_name = f"chat_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        body = (content.get("body") or "").strip()
        if not body:
            return

        user = self.scope["user"]
        message = await self._create_message(user, body)
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "chat.message",
                "message": {
                    "id": str(message.id),
                    "conversation": str(message.conversation_id),
                    "sender": str(message.sender_id),
                    "body": message.body,
                    "created_at": message.created_at.isoformat(),
                },
            },
        )

    async def chat_message(self, event):
        await self.send_json(event["message"])

    @database_sync_to_async
    def _is_participant(self, user):
        return Conversation.objects.filter(
            id=self.conversation_id, participants=user
        ).exists()

    @database_sync_to_async
    def _create_message(self, user, body):
        message = Message.objects.create(
            conversation_id=self.conversation_id, sender=user, body=body
        )
        # Destinataires = les autres participants de la conversation, jamais l'auteur. Le
        # corps du push ne contient jamais le texte du message (voir .claude/rules/
        # push-notifications.md).
        recipient_ids = message.conversation.participants.exclude(
            pk=user.pk
        ).values_list("id", flat=True)
        for recipient_id in recipient_ids:
            transaction.on_commit(
                lambda recipient_id=recipient_id: send_push_notification_task.delay(
                    user_id=recipient_id,
                    title="Nouveau message",
                    body=f"Nouveau message de {user.first_name}",
                    data={"type": "chat", "id": str(message.conversation_id)},
                )
            )
        return message
