from decimal import Decimal
from unittest.mock import patch

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.urls import re_path
from rest_framework.test import APIClient
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .consumers import CourierDispatchConsumer
from .models import Delivery
from .services import notify_nearby_couriers
from chat.middleware import JWTAuthMiddleware
from escrow.models import Order
from escrow.models import PaymentTransaction
from escrow.services.delivery_hooks import on_order_paid
from marketplace.models import Category
from marketplace.models import Listing
from users.models import User


class LogisticsTests(APITestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611100000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622100000",
            first_name="Vendeur",
            last_name="Test",
            latitude=Decimal("4.051056"),
            longitude=Decimal("9.767869"),
        )
        self.courier = User.objects.create_user(
            phone_number="+237633100000",
            first_name="Coursier",
            last_name="Test",
            is_courier=True,
        )
        self.other_courier = User.objects.create_user(
            phone_number="+237644100000",
            first_name="AutreCoursier",
            last_name="Test",
            is_courier=True,
        )
        self.staff = User.objects.create_user(
            phone_number="+237655100000",
            first_name="Staff",
            last_name="Test",
            is_staff=True,
        )
        self.cat = Category.objects.create(name="Test", slug="test")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=self.cat,
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            status="paid_escrow",
            destination_latitude=Decimal("4.061536"),
            destination_longitude=Decimal("9.786060"),
        )

    def _assign_and_advance_to(self, delivery, target_status):
        client = APIClient()
        client.force_authenticate(user=self.staff)
        client.post(
            f"/api/deliveries/{delivery.id}/assign/", {"courier": self.courier.id}
        )
        delivery.refresh_from_db()

        client.force_authenticate(user=self.courier)
        order = ["picked_up", "in_transit", "delivered"]
        for status in order:
            client.post(
                f"/api/deliveries/{delivery.id}/update-status/", {"status": status}
            )
            delivery.refresh_from_db()
            if status == target_status:
                break
        return delivery

    def test_delivery_auto_created_on_order_paid(self):
        on_order_paid(self.order)
        delivery = Delivery.objects.get(order=self.order)
        self.assertEqual(delivery.status, "pending_assignment")
        self.assertIsNone(delivery.courier)
        self.assertEqual(delivery.destination_latitude, self.order.destination_latitude)
        self.assertEqual(delivery.pickup_latitude, self.seller.latitude)

    def test_buyer_and_seller_can_view_delivery_stranger_cannot(self):
        delivery = Delivery.objects.create(order=self.order)
        stranger = User.objects.create_user(
            phone_number="+237666100000", first_name="X", last_name="Y"
        )

        client = APIClient()
        client.force_authenticate(user=self.buyer)
        self.assertEqual(client.get(f"/api/deliveries/{delivery.id}/").status_code, 200)

        client.force_authenticate(user=self.seller)
        self.assertEqual(client.get(f"/api/deliveries/{delivery.id}/").status_code, 200)

        client.force_authenticate(user=stranger)
        self.assertEqual(client.get(f"/api/deliveries/{delivery.id}/").status_code, 404)

    def test_assign_is_staff_only(self):
        delivery = Delivery.objects.create(order=self.order)
        client = APIClient()
        client.force_authenticate(user=self.buyer)
        response = client.post(
            f"/api/deliveries/{delivery.id}/assign/", {"courier": self.courier.id}
        )
        self.assertEqual(response.status_code, 403)

    def test_courier_cannot_update_others_delivery(self):
        # Le queryset scope déjà les deliveries visibles par utilisateur : un coursier non
        # assigné ne voit même pas la ressource (404), ce qui évite de confirmer son existence.
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        client = APIClient()
        client.force_authenticate(user=self.other_courier)
        response = client.post(
            f"/api/deliveries/{delivery.id}/update-status/", {"status": "picked_up"}
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_status_transition_rejected(self):
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        client = APIClient()
        client.force_authenticate(user=self.courier)
        response = client.post(
            f"/api/deliveries/{delivery.id}/update-status/", {"status": "delivered"}
        )
        self.assertEqual(response.status_code, 400)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, "assigned")

    def test_pickup_transition_marks_order_shipped(self):
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        client = APIClient()
        client.force_authenticate(user=self.courier)
        response = client.post(
            f"/api/deliveries/{delivery.id}/update-status/", {"status": "picked_up"}
        )
        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "shipped")

    def test_update_location_only_while_in_transit(self):
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        client = APIClient()
        client.force_authenticate(user=self.courier)

        response = client.post(
            f"/api/deliveries/{delivery.id}/update-location/",
            {"latitude": "4.05", "longitude": "9.70"},
        )
        self.assertEqual(response.status_code, 400)

        delivery = self._assign_and_advance_to(delivery, "in_transit")
        response = client.post(
            f"/api/deliveries/{delivery.id}/update-location/",
            {"latitude": "4.05", "longitude": "9.70"},
        )
        self.assertEqual(response.status_code, 200)
        delivery.refresh_from_db()
        self.assertEqual(delivery.courier_latitude, Decimal("4.050000"))

    def test_claim_is_atomic_only_one_courier_wins(self):
        delivery = Delivery.objects.create(
            order=self.order
        )  # pending_assignment, sans courier
        client1 = APIClient()
        client1.force_authenticate(user=self.courier)
        client2 = APIClient()
        client2.force_authenticate(user=self.other_courier)

        response1 = client1.post(f"/api/deliveries/{delivery.id}/claim/")
        response2 = client2.post(f"/api/deliveries/{delivery.id}/claim/")

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 409)
        delivery.refresh_from_db()
        self.assertEqual(delivery.courier, self.courier)
        self.assertEqual(delivery.status, "assigned")

    def test_claim_requires_courier_role(self):
        delivery = Delivery.objects.create(order=self.order)
        client = APIClient()
        client.force_authenticate(user=self.buyer)
        response = client.post(f"/api/deliveries/{delivery.id}/claim/")
        self.assertEqual(response.status_code, 403)

    def test_claim_fails_once_already_assigned(self):
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        client = APIClient()
        client.force_authenticate(user=self.other_courier)
        response = client.post(f"/api/deliveries/{delivery.id}/claim/")
        self.assertEqual(response.status_code, 409)

    def _advance_to_in_transit(self, delivery):
        client = APIClient()
        client.force_authenticate(user=self.courier)
        client.post(
            f"/api/deliveries/{delivery.id}/update-status/", {"status": "picked_up"}
        )
        client.post(
            f"/api/deliveries/{delivery.id}/update-status/", {"status": "in_transit"}
        )
        delivery.refresh_from_db()
        return delivery

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_confirm_delivery_marks_delivered_and_pays_courier_only(
        self, mock_transfer
    ):
        # Flux PR 3 côté coursier : la saisie du code fait passer la commande
        # shipped → delivered et paie le coursier, mais NE libère PAS les fonds au vendeur
        # (seule la confirmation acheteur le fait). Correctif du double reversement
        # (BLOQUANT #1) : le coursier ne clôture plus la commande.
        mock_transfer.return_value = {"transaction": {"reference": "courier-ref"}}
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        code = delivery.confirmation_code
        self._advance_to_in_transit(delivery)

        client = APIClient()
        client.force_authenticate(user=self.courier)
        response = client.post(
            f"/api/deliveries/{delivery.id}/confirm-delivery/", {"code": code}
        )
        self.assertEqual(response.status_code, 200)

        delivery.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(delivery.status, "delivered")
        # La commande est `delivered`, PAS `completed` : les fonds vendeur restent bloqués.
        self.assertEqual(self.order.status, "delivered")
        self.assertTrue(
            PaymentTransaction.objects.filter(
                order=self.order, transaction_type="courier_payout"
            ).exists()
        )
        self.assertFalse(
            PaymentTransaction.objects.filter(
                order=self.order, transaction_type="withdraw"
            ).exists()
        )

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_two_step_closure_courier_then_buyer_releases_once(self, mock_transfer):
        # Bout-en-bout : le coursier livre (delivered) puis l'acheteur confirme (completed +
        # un seul virement vendeur). Vérifie qu'un unique reversement `withdraw` est créé —
        # les deux chemins ne libèrent plus les fonds chacun de leur côté.
        # Références distinctes par appel : le payout coursier et le reversement vendeur
        # créent chacun une PaymentTransaction dont l'external_ref est unique.
        mock_transfer.side_effect = [
            {"transaction": {"reference": "courier-ref"}},
            {"transaction": {"reference": "seller-ref"}},
        ]
        delivery = Delivery.objects.create(
            order=self.order, courier=self.courier, status="assigned"
        )
        code = delivery.confirmation_code
        self._advance_to_in_transit(delivery)

        courier_client = APIClient()
        courier_client.force_authenticate(user=self.courier)
        courier_client.post(
            f"/api/deliveries/{delivery.id}/confirm-delivery/", {"code": code}
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")

        buyer_client = APIClient()
        buyer_client.force_authenticate(user=self.buyer)
        response = buyer_client.post(f"/api/orders/{self.order.id}/confirm_reception/")
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "completed")
        self.assertEqual(
            PaymentTransaction.objects.filter(
                order=self.order, transaction_type="withdraw"
            ).count(),
            1,
        )


class CourierDispatchTests(TransactionTestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            phone_number="+237622100010",
            first_name="Vendeur",
            last_name="Test",
            latitude=Decimal("4.051056"),
            longitude=Decimal("9.767869"),  # Douala
        )
        self.near_courier = User.objects.create_user(
            phone_number="+237677100010",
            first_name="Near",
            last_name="T",
            is_courier=True,
            is_available=True,
            latitude=Decimal("4.052000"),
            longitude=Decimal("9.768000"),
        )
        self.far_courier = User.objects.create_user(
            phone_number="+237677100011",
            first_name="Far",
            last_name="T",
            is_courier=True,
            is_available=True,
            latitude=Decimal("5.952700"),
            longitude=Decimal("10.146300"),  # Yaoundé
        )
        self.offline_courier = User.objects.create_user(
            phone_number="+237677100012",
            first_name="Offline",
            last_name="T",
            is_courier=True,
            is_available=False,
            latitude=Decimal("4.052000"),
            longitude=Decimal("9.768000"),
        )
        buyer = User.objects.create_user(
            phone_number="+237611100010", first_name="A", last_name="T"
        )
        cat = Category.objects.create(name="Test", slug="test-dispatch")
        listing = Listing.objects.create(
            seller=self.seller, title="Objet", description="d", price=1000, category=cat
        )
        self.order = Order.objects.create(
            buyer=buyer,
            listing=listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
            destination_latitude=Decimal("4.06"),
            destination_longitude=Decimal("9.78"),
        )
        self.delivery = Delivery.objects.create(
            order=self.order,
            pickup_latitude=self.seller.latitude,
            pickup_longitude=self.seller.longitude,
        )

    def _app(self):
        router = URLRouter(
            [
                re_path(r"^ws/courier/dispatch/$", CourierDispatchConsumer.as_asgi()),
            ]
        )
        return JWTAuthMiddleware(router)

    @database_sync_to_async
    def _token(self, user):
        return str(RefreshToken.for_user(user).access_token)

    async def test_only_nearby_available_courier_is_notified(self):
        token = await self._token(self.near_courier)
        token1 = await self._token(self.far_courier)
        token2 = await self._token(self.offline_courier)
        near_comm = WebsocketCommunicator(
            self._app(), f"/ws/courier/dispatch/?token={token}"
        )
        far_comm = WebsocketCommunicator(
            self._app(), f"/ws/courier/dispatch/?token={token1}"
        )
        offline_comm = WebsocketCommunicator(
            self._app(),
            f"/ws/courier/dispatch/?token={token2}",
        )
        self.assertTrue((await near_comm.connect())[0])
        self.assertTrue((await far_comm.connect())[0])
        self.assertTrue((await offline_comm.connect())[0])

        from channels.db import database_sync_to_async

        await database_sync_to_async(notify_nearby_couriers)(
            self.delivery, radius_km=5, limit=5
        )

        event = await near_comm.receive_json_from()
        self.assertEqual(event["id"], str(self.delivery.id))

        # far/offline ne doivent rien recevoir : receive_nothing() renvoie True si rien n'arrive.
        self.assertTrue(await far_comm.receive_nothing())
        self.assertTrue(await offline_comm.receive_nothing())

        await near_comm.disconnect()
        await far_comm.disconnect()
        await offline_comm.disconnect()

    async def test_non_courier_connection_rejected(self):
        from channels.db import database_sync_to_async

        stranger = await database_sync_to_async(User.objects.create_user)(
            phone_number="+237611100099", first_name="S", last_name="T"
        )
        token = await self._token(stranger)
        comm = WebsocketCommunicator(
            self._app(), f"/ws/courier/dispatch/?token={token}"
        )
        connected, _ = await comm.connect()
        self.assertFalse(connected)
