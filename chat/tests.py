from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.urls import re_path
from rest_framework.test import APIClient
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from chat.consumers import ChatConsumer
from chat.middleware import JWTAuthMiddleware
from chat.models import Conversation
from chat.models import Message
from marketplace.models import Category
from marketplace.models import Listing
from users.models import User


class ConversationRestTests(APITestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611200000", first_name="A", last_name="T"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622200000", first_name="V", last_name="T"
        )
        self.stranger = User.objects.create_user(
            phone_number="+237633200000", first_name="S", last_name="T"
        )
        self.cat = Category.objects.create(name="Test", slug="test")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=self.cat,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.buyer)

    def test_create_conversation_for_listing(self):
        response = self.client.post(
            "/api/chat/conversations/", {"listing": self.listing.id}
        )
        self.assertEqual(response.status_code, 201)
        conversation = Conversation.objects.get(id=response.data["id"])
        self.assertIn(self.buyer, conversation.participants.all())
        self.assertIn(self.seller, conversation.participants.all())

    def test_post_message_via_rest_history_endpoint_visible_to_participants(self):
        conversation = Conversation.objects.create(listing=self.listing)
        conversation.participants.set([self.buyer, self.seller])
        Message.objects.create(
            conversation=conversation, sender=self.buyer, body="Bonjour"
        )

        response = self.client.get(
            f"/api/chat/conversations/{conversation.id}/messages/"
        )
        self.assertEqual(response.status_code, 200)

    def test_non_participant_cannot_access_conversation(self):
        # Le queryset scope déjà les conversations visibles par utilisateur : un tiers
        # ne voit même pas la ressource (404), sans confirmer son existence.
        conversation = Conversation.objects.create(listing=self.listing)
        conversation.participants.set([self.buyer, self.seller])

        client = APIClient()
        client.force_authenticate(user=self.stranger)
        response = client.get(f"/api/chat/conversations/{conversation.id}/")
        self.assertEqual(response.status_code, 404)


class ChatWebsocketTests(TransactionTestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611300000", first_name="A", last_name="T"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622300000", first_name="V", last_name="T"
        )
        self.stranger = User.objects.create_user(
            phone_number="+237633300000", first_name="S", last_name="T"
        )
        cat = Category.objects.create(name="Test", slug="test")
        listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=cat,
        )
        self.conversation = Conversation.objects.create(listing=listing)
        self.conversation.participants.set([self.buyer, self.seller])

    def _app(self):
        router = URLRouter(
            [
                re_path(
                    r"^ws/chat/(?P<conversation_id>[0-9a-f-]{36})/$",
                    ChatConsumer.as_asgi(),
                ),
            ]
        )
        return JWTAuthMiddleware(router)

    def _token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    async def test_participant_can_connect_and_exchange_messages(self):
        token = self._token(self.buyer)
        communicator = WebsocketCommunicator(
            self._app(), f"/ws/chat/{self.conversation.id}/?token={token}"
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await communicator.send_json_to({"body": "Salut !"})
        response = await communicator.receive_json_from()
        self.assertEqual(response["body"], "Salut !")

        await communicator.disconnect()

    async def test_connection_rejected_without_token(self):
        communicator = WebsocketCommunicator(
            self._app(), f"/ws/chat/{self.conversation.id}/"
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)

    async def test_connection_rejected_for_non_participant(self):
        token = self._token(self.stranger)
        communicator = WebsocketCommunicator(
            self._app(), f"/ws/chat/{self.conversation.id}/?token={token}"
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
