import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from .models import Order
from .models import PaymentTransaction
from .serializers import SHIPPING_BASE_FEE
from .serializers import SHIPPING_FEE_PER_KM
from .services.delivery_hooks import on_order_paid
from .services.notchpay_client import NotchPayError
from .services.order_lifecycle import OrderLifecycleError
from .services.order_lifecycle import OrderLifecycleService
from .services.payouts import pay_courier_for_delivery
from .tasks import reconcile_pending_payouts
from logistics.geo import haversine_km
from logistics.models import Delivery
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
        # 1000 + 3% (30) de commission. Sans coordonnées de destination, le barème de
        # livraison retombe sur le forfait de base (SHIPPING_BASE_FEE = 500), calculé côté
        # serveur : total = 1000 + 30 + 500 = 1530.
        self.assertEqual(order.service_fee, Decimal("30.00"))
        self.assertEqual(order.shipping_fee, SHIPPING_BASE_FEE)
        self.assertEqual(order.total_amount, Decimal("1530.00"))

    def test_shipping_fee_is_computed_server_side_and_ignores_client_value(self):
        # Régression : `shipping_fee` était modifiable par le client. Il est désormais
        # read_only et recalculé par distance vendeur → destination côté serveur.
        self.seller.latitude = Decimal("4.050000")
        self.seller.longitude = Decimal("9.700000")
        self.seller.save(update_fields=["latitude", "longitude"])

        response = self.client.post(
            "/api/orders/",
            {
                "listing": self.listing.id,
                "destination_latitude": "4.060000",
                "destination_longitude": "9.710000",
                "shipping_fee": "99999.00",  # tentative d'injection : doit être ignorée
            },
        )
        self.assertEqual(response.status_code, 201)
        order = Order.objects.get(id=response.data["id"])

        distance = haversine_km(
            Decimal("4.050000"),
            Decimal("9.700000"),
            Decimal("4.060000"),
            Decimal("9.710000"),
        )
        expected = (
            SHIPPING_BASE_FEE + SHIPPING_FEE_PER_KM * Decimal(str(distance))
        ).quantize(Decimal("0.01"))

        self.assertEqual(order.shipping_fee, expected)
        self.assertNotEqual(order.shipping_fee, Decimal("99999.00"))
        self.assertEqual(
            order.total_amount,
            order.item_price + order.service_fee + order.shipping_fee,
        )

    def test_delete_verb_is_not_allowed_on_orders(self):
        # Régression : `OrderViewSet` était un `ModelViewSet` et exposait DELETE, ce qui
        # pouvait supprimer une commande (et cascader sur son journal financier).
        order = self._create_order()
        response = self.client.delete(f"/api/orders/{order.id}/")
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Order.objects.filter(id=order.id).exists())

    def test_deleting_order_with_transaction_is_protected(self):
        # Régression PR 2 : `PaymentTransaction.order` était en CASCADE ; supprimer une
        # commande effaçait silencieusement son journal financier. Le FK est désormais
        # PROTECT — supprimer une commande ayant une transaction lève une erreur
        # d'intégrité (ProtectedError ⊂ IntegrityError) au lieu de cascader.
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-protect",
            amount=1030,
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        with self.assertRaises(ProtectedError):
            order.delete()
        self.assertTrue(Order.objects.filter(id=order.id).exists())
        self.assertTrue(PaymentTransaction.objects.filter(id=txn.id).exists())

    @patch("escrow.views.NotchPayClient.initialize_payment")
    def test_initiate_payment_creates_pending_transaction(self, mock_init):
        mock_init.return_value = {"transaction": {"reference": "ref-123"}}
        order = self._create_order()
        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611000000", "channel": "mtn"},
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

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_confirm_reception_triggers_payout(self, mock_transfer):
        # Flux PR 3 : la confirmation acheteur clôture depuis `delivered` (le coursier a
        # déjà livré) → `completed` + libération des fonds au vendeur.
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="delivered")

        response = self.client.post(f"/api/orders/{order.id}/confirm_reception/")

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        self.assertTrue(
            PaymentTransaction.objects.filter(
                order=order, transaction_type="withdraw"
            ).exists()
        )

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_confirm_reception_rejected_before_delivery(self, mock_transfer):
        # Régression cycle de vie : l'acheteur ne peut confirmer la réception que depuis
        # `delivered`. Depuis `shipped` (coursier n'a pas encore saisi le code), la clôture
        # est rejetée (400) et AUCUN virement n'est déclenché.
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="shipped")

        response = self.client.post(f"/api/orders/{order.id}/confirm_reception/")

        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        mock_transfer.assert_not_called()

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_complete_and_release_is_idempotent_single_payout(self, mock_transfer):
        # Cœur de PR 3 (double reversement, BLOQUANT #1) : deux appels — séquentiels ici,
        # mais sérialisés par select_for_update en concurrent — à complete_and_release sur
        # la même commande ne déclenchent qu'UN SEUL virement vendeur. L'idempotence est
        # garantie en base par la clé unique `release:{order.id}`.
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="delivered")

        OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)
        OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        self.assertEqual(mock_transfer.call_count, 1)
        withdraws = PaymentTransaction.objects.filter(
            order=order, transaction_type="withdraw"
        )
        self.assertEqual(withdraws.count(), 1)
        self.assertEqual(withdraws.first().idempotency_key, f"release:{order.id}")
        order.refresh_from_db()
        self.assertEqual(order.status, "completed")

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_complete_and_release_rejects_invalid_status(self, mock_transfer):
        # Une clôture depuis un statut invalide (`pending`) est rejetée, sans virement.
        order = self._create_order(status="pending")

        with self.assertRaises(OrderLifecycleError):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        mock_transfer.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")
        self.assertFalse(
            PaymentTransaction.objects.filter(
                order=order, transaction_type="withdraw"
            ).exists()
        )

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_failed_release_does_not_consume_idempotency_key(self, mock_transfer):
        # Régression (⚠️ escrow-reviewer) : un échec fournisseur lors du reversement ne doit
        # PAS consommer la clé `release:{order.id}`. Sinon la commande reste `completed` sans
        # que le vendeur soit jamais payé, et toute réconciliation ultérieure devient un no-op
        # (argent bloqué). La ligne d'échec est enregistrée sans clé → une reprise reste possible.
        from escrow.services.notchpay_client import NotchPayError
        from escrow.services.payouts import release_escrow_funds

        order = self._create_order(status="delivered")

        # 1er passage : fournisseur en échec → clôture + ligne withdraw `failed` sans clé.
        mock_transfer.side_effect = NotchPayError("provider indisponible")
        OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        order.refresh_from_db()
        self.assertEqual(order.status, "completed")
        failed = PaymentTransaction.objects.filter(
            order=order, transaction_type="withdraw", status="failed"
        )
        self.assertEqual(failed.count(), 1)
        self.assertIsNone(failed.first().idempotency_key)

        # La clé n'ayant pas été consommée, une réconciliation peut re-tenter et réussir.
        mock_transfer.side_effect = None
        mock_transfer.return_value = {"transaction": {"reference": "retry-ref"}}
        release_escrow_funds(order, idempotency_key=f"release:{order.id}")

        released = PaymentTransaction.objects.filter(
            order=order,
            transaction_type="withdraw",
            idempotency_key=f"release:{order.id}",
        )
        self.assertEqual(released.count(), 1)

    def test_mark_delivered_transitions_and_is_idempotent(self):
        # `mark_delivered` : shipped → delivered, idempotent, et rejette un statut invalide.
        order = self._create_order(status="shipped")

        OrderLifecycleService.mark_delivered(order.id, actor=self.seller)
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")

        # Re-appel sur une commande déjà `delivered` : no-op sûr.
        OrderLifecycleService.mark_delivered(order.id, actor=self.seller)
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")

        # Depuis un statut invalide (`pending`) → rejet.
        pending = self._create_order(status="pending")
        with self.assertRaises(OrderLifecycleError):
            OrderLifecycleService.mark_delivered(pending.id, actor=self.seller)


class PaymentSafetyTests(APITestCase):
    """PR 5 — réconciliation des reversements, idempotence du payout coursier, signature webhook."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611000000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622000000", first_name="Vendeur", last_name="Test"
        )
        self.courier = User.objects.create_user(
            phone_number="+237633000000",
            first_name="Coursier",
            last_name="Test",
            is_courier=True,
        )
        self.cat = Category.objects.create(name="Test", slug="test")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=self.cat,
        )

    def _create_order(self, **kwargs):
        defaults = dict(
            buyer=self.buyer,
            listing=self.listing,
            item_price=Decimal("1000"),
            service_fee=Decimal("30"),
            shipping_fee=Decimal("500"),
            total_amount=Decimal("1530"),
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _age(self, txn, minutes):
        # `created_at` est auto_now_add : on le recule via un UPDATE direct (bypass).
        PaymentTransaction.objects.filter(pk=txn.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )

    # ----- Idempotence du payout coursier (point B tracké en PR 3) -----

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_pay_courier_is_idempotent_single_payout(self, mock_transfer):
        # Même modèle que test_complete_and_release_is_idempotent_single_payout : deux appels
        # ne paient le coursier qu'UNE fois, garanti en base par la clé unique
        # `courier_payout:{delivery.id}`.
        mock_transfer.return_value = {"transaction": {"reference": "courier-ref"}}
        order = self._create_order(status="delivered")
        delivery = Delivery.objects.create(
            order=order, courier=self.courier, status="delivered"
        )

        pay_courier_for_delivery(delivery)
        pay_courier_for_delivery(delivery)

        self.assertEqual(mock_transfer.call_count, 1)
        payouts = PaymentTransaction.objects.filter(
            order=order, transaction_type="courier_payout"
        )
        self.assertEqual(payouts.count(), 1)
        self.assertEqual(
            payouts.first().idempotency_key, f"courier_payout:{delivery.id}"
        )

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_failed_courier_payout_does_not_consume_key(self, mock_transfer):
        # Un échec fournisseur ne consomme PAS la clé : la ligne `failed` n'a pas de clé, donc
        # une reprise (réconciliation) reste possible sans être bloquée par la garde.
        order = self._create_order(status="delivered")
        delivery = Delivery.objects.create(
            order=order, courier=self.courier, status="delivered"
        )

        mock_transfer.side_effect = NotchPayError("provider indisponible")
        pay_courier_for_delivery(delivery)

        failed = PaymentTransaction.objects.filter(
            order=order, transaction_type="courier_payout", status="failed"
        )
        self.assertEqual(failed.count(), 1)
        self.assertIsNone(failed.first().idempotency_key)

        # La clé libre → une reprise réussit et pose enfin la clé.
        mock_transfer.side_effect = None
        mock_transfer.return_value = {"transaction": {"reference": "retry-ref"}}
        pay_courier_for_delivery(delivery)

        released = PaymentTransaction.objects.filter(
            order=order,
            transaction_type="courier_payout",
            idempotency_key=f"courier_payout:{delivery.id}",
        )
        self.assertEqual(released.count(), 1)
        self.assertEqual(released.first().status, "pending")

    # ----- Réconciliation des reversements bloqués (BLOQUANT #12) -----

    @patch("escrow.tasks.NotchPayClient.verify_transfer")
    def test_reconcile_progresses_pending_payout_to_successful(self, mock_verify):
        order = self._create_order(status="completed")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            external_ref="transfer-xyz",
            idempotency_key=f"release:{order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="pending",
        )
        self._age(txn, minutes=60)
        mock_verify.return_value = {"transfer": {"status": "complete"}}

        resolved, reinitiated = reconcile_pending_payouts()

        self.assertEqual(resolved, 1)
        self.assertEqual(mock_verify.call_count, 1)
        txn.refresh_from_db()
        self.assertEqual(txn.status, "successful")
        self.assertTrue(txn.is_success)
        # Pas de double effet de bord : toujours une seule ligne withdraw.
        self.assertEqual(
            PaymentTransaction.objects.filter(
                order=order, transaction_type="withdraw"
            ).count(),
            1,
        )

    @patch("escrow.tasks.NotchPayClient.verify_transfer")
    def test_reconcile_marks_pending_payout_failed(self, mock_verify):
        # Le fournisseur rapporte un échec définitif → la ligne pending passe `failed` de façon
        # actionnable (loggée), sans nouvel appel de virement.
        order = self._create_order(status="completed")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="courier_payout",
            external_ref="transfer-ko",
            amount=Decimal("500"),
            channel="mtn",
            phone_number="+237633000000",
            status="pending",
        )
        self._age(txn, minutes=60)
        mock_verify.return_value = {"transfer": {"status": "failed"}}

        reconcile_pending_payouts()

        txn.refresh_from_db()
        self.assertEqual(txn.status, "failed")

    @patch("escrow.tasks.NotchPayClient.verify_transfer")
    def test_reconcile_ignores_recent_pending_payout(self, mock_verify):
        # Un reversement récent (sous le seuil de 30 min) ne doit pas être touché.
        order = self._create_order(status="completed")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            external_ref="transfer-recent",
            idempotency_key=f"release:{order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="pending",
        )

        reconcile_pending_payouts()

        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.tasks.NotchPayClient.verify_transfer")
    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_reconcile_reinitiates_failed_withdraw(self, mock_transfer, mock_verify):
        # Un reversement `failed` jamais initié (fail-soft : ni ref ni clé) est re-tenté de
        # façon idempotente → une nouvelle ligne `pending` portant la clé release.
        order = self._create_order(status="completed")
        failed = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="failed",
            raw_response={"error": "payout_call_failed"},
        )
        self._age(failed, minutes=60)
        mock_transfer.return_value = {"transaction": {"reference": "retry-ref"}}

        resolved, reinitiated = reconcile_pending_payouts()

        self.assertEqual(reinitiated, 1)
        self.assertEqual(mock_transfer.call_count, 1)
        released = PaymentTransaction.objects.filter(
            order=order,
            transaction_type="withdraw",
            idempotency_key=f"release:{order.id}",
        )
        self.assertEqual(released.count(), 1)
        self.assertEqual(released.first().status, "pending")
        # La ligne re-initiée est récente → pas re-vérifiée dans le même passage.
        mock_verify.assert_not_called()

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_reconcile_does_not_reinitiate_when_already_covered(self, mock_transfer):
        # Une ligne `failed` sans clé mais une autre ligne portant déjà la clé release
        # (reversement en cours) : pas de re-tentative → aucun double paiement.
        order = self._create_order(status="completed")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            external_ref="already-pending",
            idempotency_key=f"release:{order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="pending",
        )
        failed = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="failed",
        )
        self._age(failed, minutes=60)

        _, reinitiated = reconcile_pending_payouts()

        self.assertEqual(reinitiated, 0)
        mock_transfer.assert_not_called()

    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_reconcile_stops_reinitiating_after_max_failures(self, mock_transfer):
        # Bornage : au-delà de PAYOUT_RECONCILIATION_MAX_ATTEMPTS (5) échecs cumulés, on cesse
        # de re-tenter (pas de retry infini silencieux) — loggé pour intervention manuelle.
        order = self._create_order(status="completed")
        for _ in range(5):
            f = PaymentTransaction.objects.create(
                order=order,
                transaction_type="withdraw",
                amount=Decimal("970"),
                channel="mtn",
                phone_number="+237622000000",
                status="failed",
            )
            self._age(f, minutes=60)
        mock_transfer.return_value = {"transaction": {"reference": "retry-ref"}}

        _, reinitiated = reconcile_pending_payouts()

        self.assertEqual(reinitiated, 0)
        mock_transfer.assert_not_called()

    @patch("escrow.tasks.NotchPayClient.verify_transfer")
    def test_reconcile_bounds_stuck_pending_after_max_attempts(self, mock_verify):
        # Bornage de la branche `pending` : un transfert que le fournisseur rapporte
        # indéfiniment `pending` est re-vérifié au plus PAYOUT_RECONCILIATION_MAX_ATTEMPTS (5)
        # fois, puis sort de la file (pas de retry infini silencieux).
        order = self._create_order(status="completed")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            external_ref="transfer-stuck",
            idempotency_key=f"release:{order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622000000",
            status="pending",
        )
        self._age(txn, minutes=60)
        mock_verify.return_value = {"transfer": {"status": "pending"}}

        for _ in range(5):
            reconcile_pending_payouts()
        self.assertEqual(mock_verify.call_count, 5)
        txn.refresh_from_db()
        self.assertEqual(txn.reconciliation_attempts, 5)
        self.assertEqual(txn.status, "pending")

        # 6e passage : la ligne a épuisé ses tentatives → exclue de la file, plus d'appel.
        reconcile_pending_payouts()
        self.assertEqual(mock_verify.call_count, 5)

    # ----- Signature du webhook NotchPay (MAJEUR #8) -----

    @override_settings(NOTCHPAY_WEBHOOK_HASH="whsec_test")
    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_rejects_unsigned_request(self, mock_verify):
        order = self._create_order(status="pending")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-sig",
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/",
            data=json.dumps({"reference": "ref-sig"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        mock_verify.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    @override_settings(NOTCHPAY_WEBHOOK_HASH="whsec_test")
    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_rejects_bad_signature(self, mock_verify):
        order = self._create_order(status="pending")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-sig-bad",
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/",
            data=json.dumps({"reference": "ref-sig-bad"}),
            content_type="application/json",
            HTTP_X_NOTCH_SIGNATURE="deadbeef",
        )

        self.assertEqual(response.status_code, 401)
        mock_verify.assert_not_called()

    @override_settings(NOTCHPAY_WEBHOOK_HASH="whsec_test")
    @patch("escrow.views.NotchPayClient.verify_payment")
    def test_webhook_accepts_valid_signature(self, mock_verify):
        order = self._create_order(status="pending")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-sig-ok",
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = {"transaction": {"status": "complete"}}
        body = json.dumps({"reference": "ref-sig-ok"})
        signature = hmac.new(b"whsec_test", body.encode(), hashlib.sha256).hexdigest()
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/",
            data=body,
            content_type="application/json",
            HTTP_X_NOTCH_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")


class PushNotificationHooksTests(APITestCase):
    """BE-PUSH-2 — branchement des pushs sur le cycle de vie de la commande (voir
    PUSH_NOTIFICATIONS_PLAN.md). Seule la planification Celery (`.delay`) est vérifiée ici ;
    l'envoi réel est déjà couvert par notifications/tests.py (BE-PUSH-1)."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611900000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622900000", first_name="Vendeur", last_name="Test"
        )
        self.cat = Category.objects.create(name="Test", slug="test-push")
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Objet",
            description="desc",
            price=1000,
            category=self.cat,
        )

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

    @patch("escrow.services.delivery_hooks.send_push_notification_task.delay")
    def test_order_paid_notifies_seller(self, mock_delay):
        order = self._create_order(status="paid_escrow")

        # Le push n'est planifié qu'après commit (transaction.on_commit) — captureOnCommit-
        # Callbacks simule ce commit sans dépendre d'une vraie transaction DB en cours.
        with self.captureOnCommitCallbacks(execute=True):
            on_order_paid(order)

        mock_delay.assert_called_once_with(
            user_id=self.seller.id,
            title="Nouvelle commande payée",
            body="Nouvelle commande payée — prépare l'envoi",
            data={"type": "order", "id": str(order.id)},
        )

    @patch("escrow.services.order_lifecycle.send_push_notification_task.delay")
    def test_mark_delivered_notifies_buyer(self, mock_delay):
        order = self._create_order(status="shipped")

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.mark_delivered(order.id, actor=self.buyer)

        mock_delay.assert_called_once_with(
            user_id=self.buyer.id,
            title="Commande livrée",
            body="Ta commande est arrivée — confirme la réception",
            data={"type": "order", "id": str(order.id)},
        )

    @patch("escrow.services.order_lifecycle.send_push_notification_task.delay")
    def test_mark_delivered_no_renotification_on_idempotent_replay(self, mock_delay):
        # Régression : un second appel sur une commande déjà `delivered` est un no-op — il
        # ne doit pas renvoyer une notification à l'acheteur.
        order = self._create_order(status="delivered")

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.mark_delivered(order.id, actor=self.buyer)

        mock_delay.assert_not_called()

    @patch("escrow.services.order_lifecycle.send_push_notification_task.delay")
    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_complete_and_release_notifies_seller(self, mock_transfer, mock_delay):
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="delivered")

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        mock_delay.assert_called_once_with(
            user_id=self.seller.id,
            title="Paiement reçu",
            body="Tu as été payé pour ton annonce",
            data={"type": "order", "id": str(order.id)},
        )

    @patch("escrow.services.order_lifecycle.send_push_notification_task.delay")
    @patch("escrow.services.payouts.NotchPayClient.initialize_transfer")
    def test_complete_and_release_no_renotification_on_idempotent_replay(
        self, mock_transfer, mock_delay
    ):
        # Symétrique de test_mark_delivered_no_renotification_on_idempotent_replay : un
        # second appel sur une commande déjà `completed` (fonds déjà libérés) est un no-op
        # sûr — il ne doit pas renvoyer une seconde notification au vendeur.
        mock_transfer.return_value = {"transaction": {"reference": "payout-ref"}}
        order = self._create_order(status="delivered")

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        mock_delay.assert_not_called()
