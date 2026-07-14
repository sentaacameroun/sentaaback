from decimal import Decimal
from unittest.mock import patch

from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from .models import Order
from .models import PaymentTransaction
from marketplace.models import Category
from marketplace.models import Listing
from users.models import User


class EscrowTests(APITestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611000000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622000000", first_name="Vendeur", last_name="Test"
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

    def _create_order(self, **kwargs):
        defaults = dict(
            buyer=self.buyer,
            listing=self.listing,
            item_price=1000,
            service_fee=30,
            total_amount=1030,
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def test_commission_calculation(self):
        response = self.client.post("/api/orders/", {"listing": self.listing.id})
        order = Order.objects.get(id=response.data["id"])
        # 1000 + 3% (30) = 1030
        self.assertEqual(order.service_fee, Decimal("30.00"))
        self.assertEqual(order.total_amount, Decimal("1030.00"))

    @patch("escrow.views.NotchPayClient.initialize_payment")
    def test_initiate_payment_creates_pending_transaction(self, mock_init):
        mock_init.return_value = {"transaction": {"reference": "ref-123"}}
        order = self._create_order()
        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611000000", "channel": "cm.mtn"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            PaymentTransaction.objects.filter(order=order, status="pending").count(), 1
        )

    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_marks_order_paid_on_success(self, mock_verify):
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-abc",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = {"transaction": {"status": "complete"}}

        client = APIClient()  # webhook non authentifié
        response = client.post("/api/mobile-money-webhook/", {"reference": "ref-abc"})

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")
        self.assertEqual(txn.status, "successful")

    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_ignores_unconfirmed_status(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-pending",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = {"transaction": {"status": "pending"}}

        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/", {"reference": "ref-pending"}
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_is_idempotent_on_duplicate_calls(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-dup",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = {"transaction": {"status": "complete"}}

        client = APIClient()
        client.post("/api/mobile-money-webhook/", {"reference": "ref-dup"})
        client.post("/api/mobile-money-webhook/", {"reference": "ref-dup"})

        self.assertEqual(mock_verify.call_count, 1)

    @patch("escrow.views.NotchPayClient.initialize_transfer")
    def test_confirm_reception_triggers_payout(self, mock_transfer):
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="shipped")

        response = self.client.post(f"/api/orders/{order.id}/confirm_reception/")

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertTrue(
            PaymentTransaction.objects.filter(
                order=order, transaction_type="withdraw"
            ).exists()
        )
