import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import override_settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.test import APITestCase

from .models import Order
from .models import PaymentTransaction
from .serializers import SHIPPING_BASE_FEE
from .serializers import SHIPPING_FEE_PER_KM
from .services.delivery_hooks import on_order_paid
from .services.order_lifecycle import OrderLifecycleError
from .services.order_lifecycle import OrderLifecycleService
from .services.payouts import pay_courier_for_delivery
from .services.payouts import release_escrow_funds
from .services.providers import FLOW_REDIRECT
from .services.providers import FLOW_USSD
from .services.providers import get_payment_client
from .services.providers import KPayClient
from .services.providers import KPayError
from .services.providers import NotchPayClient
from .services.providers import NotchPayError
from .services.providers import PaymentProviderError
from .services.providers import ProviderResult
from .services.providers import STATUS_FAILED
from .services.providers import STATUS_PENDING
from .services.providers import STATUS_SUCCESSFUL
from .tasks import reconcile_pending_payouts
from logistics.geo import haversine_km
from logistics.models import Delivery
from marketplace.models import Category
from marketplace.models import Listing
from users.models import User


@override_settings(PAYMENT_PROVIDER="notchpay")
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_payment")
    def test_initiate_payment_creates_pending_transaction(self, mock_init):
        mock_init.return_value = ProviderResult(
            provider_reference="notch-ref-123",
            status=STATUS_PENDING,
            payment_flow=FLOW_REDIRECT,
            checkout_url="https://pay.notchpay.co/notch-ref-123",
            raw={
                "transaction": {
                    "reference": "notch-ref-123",
                    "authorization_url": "https://pay.notchpay.co/notch-ref-123",
                    "customer": "cus_should_never_leak_to_frontend",
                }
            },
        )
        order = self._create_order()
        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611000000", "channel": "mtn"},
        )
        self.assertEqual(response.status_code, 202)
        txn = PaymentTransaction.objects.get(order=order, status="pending")
        # `provider` trace le fournisseur ayant réellement traité l'opération (architecture
        # multi-provider) — nécessaire à la réconciliation (escrow/tasks.py) pour interroger
        # le bon fournisseur même si PAYMENT_PROVIDER change ensuite.
        self.assertEqual(txn.provider, "notchpay")
        # `provider_reference` stockée dès la création : c'est elle que le webhook comparera
        # à celle reçue dans son payload avant de faire confiance à `verify_payment` (voir
        # _CollectWebhookView._process et PaymentSafetyTests ci-dessous).
        self.assertEqual(txn.provider_reference, "notch-ref-123")
        # La réponse au frontend ne doit JAMAIS exposer l'objet brut du fournisseur (ex.
        # `customer`) — seulement ce qui est utile pour compléter le paiement. `payment_flow`
        # est le signal explicite que le frontend doit tester (pas la seule présence de
        # `checkout_url`) pour savoir s'il doit ouvrir un lien ou attendre un push USSD.
        self.assertEqual(
            response.data,
            {
                "reference": txn.external_ref,
                "status": "pending",
                "payment_flow": "redirect",
                "checkout_url": "https://pay.notchpay.co/notch-ref-123",
            },
        )
        self.assertNotIn("provider_response", response.data)
        self.assertNotIn("raw", response.data)

    @patch("escrow.services.providers.kpay.KPayClient.initialize_payment")
    @override_settings(PAYMENT_PROVIDER="kpay")
    def test_initiate_payment_omits_checkout_url_in_ussd_flow(self, mock_init):
        # Mode USSD (push direct, KPay) : pas de page hébergée, donc pas de `checkout_url` —
        # la clé ne doit même pas apparaître dans la réponse plutôt que d'envoyer `None`, et
        # `payment_flow` doit permettre au frontend de le savoir sans avoir à le déduire.
        mock_init.return_value = ProviderResult(
            provider_reference="pay_ussd",
            status=STATUS_PENDING,
            payment_flow=FLOW_USSD,
            raw={"id": "pay_ussd", "status": "PENDING"},
        )
        order = self._create_order()
        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611000000", "channel": "mtn"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["payment_flow"], "ussd")
        self.assertNotIn("checkout_url", response.data)

    def test_initiate_payment_rejects_partial_channel_phone_pair(self):
        # Cas invalide : channel sans phone_number (ou l'inverse) n'est ni un paiement direct
        # complet ni une demande explicite de mode page hébergée — rejeté (400).
        order = self._create_order()
        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/", {"channel": "mtn"}
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611000000"},
        )
        self.assertEqual(response.status_code, 400)

    def test_initiate_payment_requires_return_url_for_hosted_page_flow(self):
        # Ni channel/phone_number, ni return_url : impossible de savoir où rediriger
        # l'acheteur après paiement — rejeté (400), avant tout appel fournisseur.
        order = self._create_order()
        response = self.client.post(f"/api/orders/{order.id}/initiate_payment/", {})
        self.assertEqual(response.status_code, 400)

    @override_settings(PAYMENT_PROVIDER="kpay")
    @patch("escrow.services.providers.kpay.KPayClient.initialize_payment")
    def test_initiate_payment_gateway_flow_omits_channel_and_phone(self, mock_init):
        # Demande explicite de mode page hébergée (KPay : GATEWAY, Mobile Money + cartes/
        # PayPal) : ni channel ni phone_number envoyés, `return_url` fournie par le frontend.
        mock_init.return_value = ProviderResult(
            provider_reference="pay_gateway",
            status=STATUS_PENDING,
            payment_flow=FLOW_REDIRECT,
            checkout_url="https://pay.kpay.cm/pay/sandbox/abc",
            raw={
                "id": "pay_gateway",
                "gatewayUrl": "https://pay.kpay.cm/pay/sandbox/abc",
            },
        )
        order = self._create_order()

        response = self.client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {
                "return_url": "https://app.sentaa.net/paiement/retour",
                "cancel_url": "https://app.sentaa.net/paiement/annule",
            },
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.data,
            {
                "reference": PaymentTransaction.objects.get(order=order).external_ref,
                "status": "pending",
                "payment_flow": "redirect",
                "checkout_url": "https://pay.kpay.cm/pay/sandbox/abc",
            },
        )
        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        self.assertIsNone(call_kwargs["channel"])
        self.assertIsNone(call_kwargs["phone"])
        self.assertEqual(
            call_kwargs["return_url"], "https://app.sentaa.net/paiement/retour"
        )
        txn = PaymentTransaction.objects.get(order=order)
        # Ni channel ni phone_number connus pour une collecte par carte/PayPal — stockés
        # vides plutôt qu'une valeur inventée (voir escrow/services/payouts.py pour le
        # repli au moment du reversement).
        self.assertEqual(txn.channel, "")
        self.assertEqual(txn.phone_number, "")

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_payment")
    def test_initiate_payment_logs_warning_when_provider_reference_missing(
        self, mock_init
    ):
        # Revue escrow-reviewer : une réponse fournisseur sans provider_reference désactive
        # silencieusement le garde-fou anti-rejeu du webhook pour cette transaction — doit au
        # moins être loggé pour rester observable, plutôt que de passer totalement inaperçu.
        mock_init.return_value = ProviderResult(
            provider_reference=None,
            status=STATUS_PENDING,
            raw={"transaction": {}},
        )
        order = self._create_order()

        with self.assertLogs("escrow.views", level="WARNING") as logs:
            response = self.client.post(
                f"/api/orders/{order.id}/initiate_payment/",
                {"phone_number": "+237611000000", "channel": "mtn"},
            )

        self.assertEqual(response.status_code, 202)
        self.assertTrue(any("provider_reference" in message for message in logs.output))
        self.assertEqual(
            PaymentTransaction.objects.get(order=order).provider_reference, ""
        )

    @staticmethod
    def _notchpay_webhook_body(merchant_reference, provider_reference=None):
        # Forme réelle du webhook NotchPay (imbriquée sous "data") — voir la régression
        # corrigée par les commits cc52484/1abd301 : les anciens tests posaient un payload
        # à plat ({"reference": ...}) qui ne correspond pas à ce que NotchPay envoie
        # réellement, ce qui faisait échouer ces tests en silence (400) sans que la vue soit
        # en cause.
        return json.dumps(
            {
                "event": "payment.complete",
                "data": {
                    "merchant_reference": merchant_reference,
                    "reference": provider_reference or f"notch-{merchant_reference}",
                },
            }
        )

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_marks_order_paid_on_success(self, mock_verify):
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-abc",
            provider="notchpay",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="notch-ref-abc",
            status=STATUS_SUCCESSFUL,
            raw={"transaction": {"status": "complete"}},
        )

        client = APIClient()  # webhook non authentifié
        response = client.post(
            "/api/mobile-money-webhook/",
            data=self._notchpay_webhook_body("ref-abc"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")
        self.assertEqual(txn.status, "successful")

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_ignores_unconfirmed_status(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-pending",
            provider="notchpay",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference=None,
            status=STATUS_PENDING,
            raw={"transaction": {"status": "pending"}},
        )

        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/",
            data=self._notchpay_webhook_body("ref-pending"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_is_idempotent_on_duplicate_calls(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-dup",
            provider="notchpay",
            amount=1030,
            channel="cm.mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="notch-ref-dup",
            status=STATUS_SUCCESSFUL,
            raw={"transaction": {"status": "complete"}},
        )

        client = APIClient()
        body = self._notchpay_webhook_body("ref-dup")
        client.post(
            "/api/mobile-money-webhook/", data=body, content_type="application/json"
        )
        client.post(
            "/api/mobile-money-webhook/", data=body, content_type="application/json"
        )

        self.assertEqual(mock_verify.call_count, 1)

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_confirm_reception_triggers_payout(self, mock_transfer):
        # Flux PR 3 : la confirmation acheteur clôture depuis `delivered` (le coursier a
        # déjà livré) → `completed` + libération des fonds au vendeur.
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_confirm_reception_rejected_before_delivery(self, mock_transfer):
        # Régression cycle de vie : l'acheteur ne peut confirmer la réception que depuis
        # `delivered`. Depuis `shipped` (coursier n'a pas encore saisi le code), la clôture
        # est rejetée (400) et AUCUN virement n'est déclenché.
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
        order = self._create_order(status="shipped")

        response = self.client.post(f"/api/orders/{order.id}/confirm_reception/")

        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        mock_transfer.assert_not_called()

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_complete_and_release_is_idempotent_single_payout(self, mock_transfer):
        # Cœur de PR 3 (double reversement, BLOQUANT #1) : deux appels — séquentiels ici,
        # mais sérialisés par select_for_update en concurrent — à complete_and_release sur
        # la même commande ne déclenchent qu'UN SEUL virement vendeur. L'idempotence est
        # garantie en base par la clé unique `release:{order.id}`.
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_failed_release_does_not_consume_idempotency_key(self, mock_transfer):
        # Régression (⚠️ escrow-reviewer) : un échec fournisseur lors du reversement ne doit
        # PAS consommer la clé `release:{order.id}`. Sinon la commande reste `completed` sans
        # que le vendeur soit jamais payé, et toute réconciliation ultérieure devient un no-op
        # (argent bloqué). La ligne d'échec est enregistrée sans clé → une reprise reste possible.
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
        mock_transfer.return_value = ProviderResult(
            provider_reference="retry-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "retry-ref"}},
        )
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


@override_settings(PAYMENT_PROVIDER="notchpay")
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

    # ----- Repli de canal pour une collecte sans opérateur Mobile Money connu -----
    # (paiement par carte/PayPal via la page hébergée KPay, voir InitiatePaymentSerializer)

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_release_escrow_funds_falls_back_when_collect_channel_blank(
        self, mock_transfer
    ):
        # Un `channel` vide sur la collecte (carte/PayPal) ne doit jamais être propagé tel
        # quel à `initialize_transfer` : chez KPay, un channel vide ferait échouer l'appel
        # (KeyError → KPayError) à chaque tentative de réconciliation, sans jamais aboutir.
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
        order = self._create_order(status="delivered")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-card",
            channel="",
            phone_number="",
            amount=order.total_amount,
            status="successful",
        )

        release_escrow_funds(order, idempotency_key=f"release:{order.id}")

        self.assertEqual(
            mock_transfer.call_args.kwargs["channel"], PaymentTransaction.CHANNELS[0][0]
        )

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_pay_courier_falls_back_when_collect_channel_blank(self, mock_transfer):
        mock_transfer.return_value = ProviderResult(
            provider_reference="courier-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "courier-ref"}},
        )
        order = self._create_order(status="delivered")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-card-courier",
            channel="",
            phone_number="",
            amount=order.total_amount,
            status="successful",
        )
        delivery = Delivery.objects.create(
            order=order, courier=self.courier, status="delivered"
        )

        pay_courier_for_delivery(delivery)

        self.assertEqual(
            mock_transfer.call_args.kwargs["channel"], PaymentTransaction.CHANNELS[0][0]
        )

    # ----- Idempotence du payout coursier (point B tracké en PR 3) -----

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_pay_courier_is_idempotent_single_payout(self, mock_transfer):
        # Même modèle que test_complete_and_release_is_idempotent_single_payout : deux appels
        # ne paient le coursier qu'UNE fois, garanti en base par la clé unique
        # `courier_payout:{delivery.id}`.
        mock_transfer.return_value = ProviderResult(
            provider_reference="courier-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "courier-ref"}},
        )
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
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
        mock_transfer.return_value = ProviderResult(
            provider_reference="retry-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "retry-ref"}},
        )
        pay_courier_for_delivery(delivery)

        released = PaymentTransaction.objects.filter(
            order=order,
            transaction_type="courier_payout",
            idempotency_key=f"courier_payout:{delivery.id}",
        )
        self.assertEqual(released.count(), 1)
        self.assertEqual(released.first().status, "pending")

    # ----- Réconciliation des reversements bloqués (BLOQUANT #12) -----

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
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
        mock_verify.return_value = ProviderResult(
            provider_reference="transfer-xyz",
            status=STATUS_SUCCESSFUL,
            raw={"transfer": {"status": "complete"}},
        )

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

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
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
        mock_verify.return_value = ProviderResult(
            provider_reference="transfer-ko",
            status=STATUS_FAILED,
            raw={"transfer": {"status": "failed"}},
        )

        reconcile_pending_payouts()

        txn.refresh_from_db()
        self.assertEqual(txn.status, "failed")

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
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
        mock_transfer.return_value = ProviderResult(
            provider_reference="retry-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "retry-ref"}},
        )

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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
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
        mock_transfer.return_value = ProviderResult(
            provider_reference="retry-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "retry-ref"}},
        )

        _, reinitiated = reconcile_pending_payouts()

        self.assertEqual(reinitiated, 0)
        mock_transfer.assert_not_called()

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
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
        mock_verify.return_value = ProviderResult(
            provider_reference="transfer-stuck",
            status=STATUS_PENDING,
            raw={"transfer": {"status": "pending"}},
        )

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
    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
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
    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
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
    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
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
        mock_verify.return_value = ProviderResult(
            provider_reference="notch-ref-sig-ok",
            status=STATUS_SUCCESSFUL,
            raw={"transaction": {"status": "complete"}},
        )
        body = json.dumps(
            {
                "event": "payment.complete",
                "data": {
                    "merchant_reference": "ref-sig-ok",
                    "reference": "notch-ref-sig-ok",
                },
            }
        )
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

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_ignores_transaction_owned_by_another_provider(self, mock_verify):
        # Symétrique de KPayWebhookTests.test_webhook_ignores_transaction_owned_by_another_provider :
        # une référence provider valide chez NotchPay ne doit jamais clôturer une transaction
        # traitée par KPay.
        order = self._create_order(status="pending")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-cross-provider-notch",
            provider="kpay",  # traitée par KPay, pas NotchPay
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )

        body = json.dumps(
            {
                "event": "payment.complete",
                "data": {
                    "merchant_reference": "ref-cross-provider-notch",
                    "reference": "notch-ref-cross-provider",
                },
            }
        )
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/", data=body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_rejects_replayed_provider_reference(self, mock_verify):
        # Cœur du correctif (revue escrow-reviewer) : un `provider_reference` RÉEL et
        # effectivement complété chez NotchPay (donc `verify_payment` réussirait) mais
        # appartenant à un AUTRE paiement ne doit jamais pouvoir clôturer cette transaction —
        # seul celui enregistré à la création (`initiate_payment`) est accepté. Sans ce
        # garde-fou, un attaquant pourrait rejouer la référence d'un paiement minime qu'il a
        # réellement payé pour faire créditer une commande bien plus chère jamais payée.
        order = self._create_order(status="pending")
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-replay",
            provider="notchpay",
            provider_reference="notch-ref-legit",  # celle attribuée à CE paiement
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        body = json.dumps(
            {
                "event": "payment.complete",
                "data": {
                    "merchant_reference": "ref-replay",
                    # rejouée depuis un AUTRE paiement, réellement complété chez l'attaquant
                    "reference": "notch-ref-from-another-real-payment",
                },
            }
        )
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/", data=body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_payment")
    def test_webhook_accepts_matching_provider_reference(self, mock_verify):
        # Cas valide symétrique : le `provider_reference` reçu correspond bien à celui
        # enregistré à la création → traitement normal, pas de faux positif introduit par le
        # correctif.
        order = self._create_order(status="pending")
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-match",
            provider="notchpay",
            provider_reference="notch-ref-match",
            amount=Decimal("1530"),
            channel="mtn",
            phone_number="+237611000000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="notch-ref-match",
            status=STATUS_SUCCESSFUL,
            raw={"transaction": {"status": "complete"}},
        )
        body = json.dumps(
            {
                "event": "payment.complete",
                "data": {
                    "merchant_reference": "ref-match",
                    "reference": "notch-ref-match",
                },
            }
        )
        client = APIClient()
        response = client.post(
            "/api/mobile-money-webhook/", data=body, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("notch-ref-match")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")


@override_settings(PAYMENT_PROVIDER="notchpay")
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
    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_complete_and_release_notifies_seller(self, mock_transfer, mock_delay):
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
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
    @patch("escrow.services.providers.notchpay.NotchPayClient.initialize_transfer")
    def test_complete_and_release_no_renotification_on_idempotent_replay(
        self, mock_transfer, mock_delay
    ):
        # Symétrique de test_mark_delivered_no_renotification_on_idempotent_replay : un
        # second appel sur une commande déjà `completed` (fonds déjà libérés) est un no-op
        # sûr — il ne doit pas renvoyer une seconde notification au vendeur.
        mock_transfer.return_value = ProviderResult(
            provider_reference="payout-ref",
            status=STATUS_PENDING,
            raw={"transaction": {"reference": "payout-ref"}},
        )
        order = self._create_order(status="delivered")

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            OrderLifecycleService.complete_and_release(order.id, actor=self.buyer)

        mock_delay.assert_not_called()


class PaymentProviderFactoryTests(TestCase):
    """Architecture multi-provider (escrow/services/providers/) : sélection du fournisseur
    actif via `settings.PAYMENT_PROVIDER`."""

    @override_settings(PAYMENT_PROVIDER="kpay")
    def test_selects_kpay_by_default_when_configured(self):
        client = get_payment_client()
        self.assertIsInstance(client, KPayClient)
        self.assertEqual(client.name, "kpay")

    @override_settings(PAYMENT_PROVIDER="notchpay")
    def test_selects_notchpay_when_configured(self):
        client = get_payment_client()
        self.assertIsInstance(client, NotchPayClient)
        self.assertEqual(client.name, "notchpay")

    @override_settings(PAYMENT_PROVIDER="notchpay")
    def test_explicit_provider_name_overrides_active_setting(self):
        # Utilisé par la réconciliation (escrow/tasks.py) : chaque PaymentTransaction garde
        # le fournisseur qui l'a réellement traitée, indépendamment du fournisseur ACTUELLEMENT
        # actif.
        client = get_payment_client("kpay")
        self.assertIsInstance(client, KPayClient)

    @override_settings(PAYMENT_PROVIDER="kpay")
    def test_blank_provider_name_falls_back_to_active_setting(self):
        # Lignes créées avant l'ajout du champ `PaymentTransaction.provider` (blank=True) :
        # une chaîne vide ne doit pas être traitée comme un nom de fournisseur.
        client = get_payment_client("")
        self.assertIsInstance(client, KPayClient)

    def test_unknown_provider_name_raises(self):
        # Cas invalide : une valeur de PAYMENT_PROVIDER mal configurée ne doit jamais
        # instancier silencieusement un mauvais client.
        with self.assertRaises(PaymentProviderError):
            get_payment_client("wire_transfer")


class KPayClientTests(TestCase):
    """Client KPay (intégrateur principal, https://kpay.site/documentation) — mappage des
    canaux, normalisation des statuts, gestion des erreurs. `requests.request` est mocké au
    niveau HTTP : contrairement aux tests qui patchent les méthodes publiques du client
    (EscrowTests, PaymentSafetyTests), ceux-ci exercent réellement le code de traduction
    propre à KPay."""

    def _mock_response(self, status_code=200, payload=None):
        response = Mock()
        response.ok = 200 <= status_code < 300
        response.status_code = status_code
        response.json.return_value = payload or {}
        response.text = str(payload or {})
        return response

    def test_unsupported_channel_raises_before_any_request(self):
        with patch("escrow.services.providers.kpay.requests.request") as mock_request:
            with self.assertRaises(KPayError):
                KPayClient().initialize_payment(
                    amount=1000,
                    phone="237670000001",
                    currency="XAF",
                    reference="ref-1",
                    channel="visa",  # canal non supporté
                )
        mock_request.assert_not_called()

    @patch("escrow.services.providers.kpay.requests.request")
    def test_initialize_payment_maps_channel_and_normalizes_pending_status(
        self, mock_request
    ):
        mock_request.return_value = self._mock_response(
            201,
            {
                "id": "pay_abc123",
                "reference": "KPAY-20260514-ABC123",
                "status": "PENDING",
                "amount": 5000,
            },
        )

        result = KPayClient().initialize_payment(
            amount=5000,
            phone="237670000001",
            currency="XAF",
            reference="SENTAA-REF-1",
            channel="mtn",
            description="Paiement escrow commande X",
        )

        self.assertEqual(result.status, STATUS_PENDING)
        self.assertEqual(result.provider_reference, "pay_abc123")
        self.assertEqual(result.payment_flow, FLOW_USSD)
        sent_payload = mock_request.call_args.kwargs["json"]
        self.assertEqual(sent_payload["provider"], "MTN_MOMO_CMR")
        self.assertEqual(sent_payload["externalId"], "SENTAA-REF-1")
        self.assertEqual(sent_payload["phoneNumber"], "237670000001")

    @patch("escrow.services.providers.kpay.requests.request")
    def test_initialize_payment_gateway_mode_when_no_channel(self, mock_request):
        # Pas de `channel` fourni : mode GATEWAY
        # (https://kpay.site/documentation/paiements) — ni `provider` ni `phoneNumber` dans
        # le payload envoyé, `returnUrl`/`cancelUrl` à la place ; la réponse expose
        # `gatewayUrl`, repris comme `checkout_url`.
        mock_request.return_value = self._mock_response(
            201,
            {
                "id": "pay_gateway123",
                "status": "PENDING",
                "mode": "GATEWAY",
                "gatewayUrl": "https://pay.kpay.cm/pay/sandbox/abc",
            },
        )

        result = KPayClient().initialize_payment(
            amount=5000,
            phone=None,
            currency="XAF",
            reference="SENTAA-REF-2",
            channel=None,
            return_url="https://app.sentaa.net/paiement/retour",
            cancel_url="https://app.sentaa.net/paiement/annule",
        )

        self.assertEqual(result.payment_flow, FLOW_REDIRECT)
        self.assertEqual(result.checkout_url, "https://pay.kpay.cm/pay/sandbox/abc")
        sent_payload = mock_request.call_args.kwargs["json"]
        self.assertNotIn("provider", sent_payload)
        self.assertNotIn("phoneNumber", sent_payload)
        self.assertEqual(
            sent_payload["returnUrl"], "https://app.sentaa.net/paiement/retour"
        )
        self.assertEqual(
            sent_payload["cancelUrl"], "https://app.sentaa.net/paiement/annule"
        )

    def test_initialize_payment_gateway_mode_requires_return_url(self):
        # Garde-fou défensif (la validation normale se fait en amont dans
        # InitiatePaymentSerializer) : un appel direct au client sans channel ni return_url
        # doit échouer explicitement plutôt que d'appeler KPay avec un payload invalide.
        with patch("escrow.services.providers.kpay.requests.request") as mock_request:
            with self.assertRaises(KPayError):
                KPayClient().initialize_payment(
                    amount=5000,
                    phone=None,
                    currency="XAF",
                    reference="SENTAA-REF-3",
                    channel=None,
                )
        mock_request.assert_not_called()

    @patch("escrow.services.providers.kpay.requests.request")
    def test_verify_payment_normalizes_completed_status(self, mock_request):
        mock_request.return_value = self._mock_response(
            200, {"id": "pay_abc123", "status": "COMPLETED"}
        )

        result = KPayClient().verify_payment("pay_abc123")

        self.assertEqual(result.status, STATUS_SUCCESSFUL)

    @patch("escrow.services.providers.kpay.requests.request")
    def test_verify_payment_normalizes_failed_status(self, mock_request):
        mock_request.return_value = self._mock_response(
            200, {"id": "pay_abc123", "status": "FAILED"}
        )

        result = KPayClient().verify_payment("pay_abc123")

        self.assertEqual(result.status, STATUS_FAILED)

    @patch("escrow.services.providers.kpay.requests.request")
    def test_initialize_transfer_includes_external_id_only_when_given(
        self, mock_request
    ):
        mock_request.return_value = self._mock_response(
            201, {"id": "wdr_xyz456", "status": "PENDING"}
        )
        client = KPayClient()

        client.initialize_transfer(
            amount=970,
            account_number="237622000000",
            channel="orange",
            currency="XAF",
            reference="release:order-1",
        )
        self.assertEqual(
            mock_request.call_args.kwargs["json"]["externalId"], "release:order-1"
        )
        self.assertEqual(
            mock_request.call_args.kwargs["json"]["provider"], "ORANGE_CMR"
        )

        client.initialize_transfer(
            amount=970,
            account_number="237622000000",
            channel="orange",
            currency="XAF",
        )
        self.assertNotIn("externalId", mock_request.call_args.kwargs["json"])

    @patch("escrow.services.providers.kpay.requests.request")
    def test_http_error_raises_kpay_error(self, mock_request):
        mock_request.return_value = self._mock_response(
            400, {"message": "Invalid amount"}
        )

        with self.assertRaises(KPayError):
            KPayClient().verify_transfer("wdr_xyz456")


class NotchPayClientTests(TestCase):
    """Client NotchPay (fournisseur historique) : la normalisation vers `ProviderResult` a
    été introduite en déplaçant ce client dans `escrow/services/providers/` — ces tests
    exercent le vrai code de parsing (contrairement à EscrowTests/PaymentSafetyTests, qui
    mockent la méthode publique elle-même)."""

    def _mock_response(self, status_code=200, payload=None):
        response = Mock()
        response.ok = 200 <= status_code < 300
        response.status_code = status_code
        response.json.return_value = payload or {}
        response.text = str(payload or {})
        return response

    @patch("escrow.services.providers.notchpay.requests.request")
    def test_initialize_payment_extracts_transaction_reference(self, mock_request):
        mock_request.return_value = self._mock_response(
            200, {"transaction": {"reference": "notch-ref-1", "status": "pending"}}
        )

        result = NotchPayClient().initialize_payment(
            amount=1000, phone="+237611000000", currency="XAF", reference="SENTAA-1"
        )

        self.assertEqual(result.provider_reference, "notch-ref-1")
        self.assertEqual(result.status, STATUS_PENDING)

    @patch("escrow.services.providers.notchpay.requests.request")
    def test_verify_transfer_parses_nested_transfer_key(self, mock_request):
        mock_request.return_value = self._mock_response(
            200, {"transfer": {"reference": "transfer-1", "status": "complete"}}
        )

        result = NotchPayClient().verify_transfer("transfer-1")

        self.assertEqual(result.status, STATUS_SUCCESSFUL)


@override_settings(PAYMENT_PROVIDER="kpay")
class KPayWebhookTests(APITestCase):
    """Webhook KPay (intégrateur principal) — miroir des tests de signature NotchPay
    (PaymentSafetyTests), pour le nouvel endpoint `/kpay-webhook/`."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611800000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622800000", first_name="Vendeur", last_name="Test"
        )
        self.cat = Category.objects.create(name="Test", slug="test-kpay")
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

    @staticmethod
    def _body(external_id, payment_id="pay_abc123", event="payment.completed"):
        return json.dumps(
            {"event": event, "externalId": external_id, "paymentId": payment_id}
        )

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_marks_order_paid_on_success(self, mock_verify):
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-1",
            provider="kpay",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="pay_abc123",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=self._body("ref-kpay-1"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")
        self.assertEqual(txn.status, "successful")

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_is_idempotent_on_duplicate_calls(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-dup",
            provider="kpay",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="pay_dup",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )

        client = APIClient()
        body = self._body("ref-kpay-dup")
        client.post("/api/kpay-webhook/", data=body, content_type="application/json")
        client.post("/api/kpay-webhook/", data=body, content_type="application/json")

        self.assertEqual(mock_verify.call_count, 1)

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_ignores_non_payment_events(self, mock_verify):
        # Les événements retrait/remboursement (`payout.*`, `refund.*`) ne sont pas
        # consommés par ce webhook (convergence assurée par la réconciliation Celery) —
        # aucun appel réseau, aucune écriture.
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-payout",
            provider="kpay",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=self._body("ref-kpay-payout", event="payout.completed"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_ignores_transaction_owned_by_another_provider(self, mock_verify):
        # Revue escrow-reviewer (PR 7) : une référence provider valide chez KPay ne doit
        # jamais pouvoir clôturer une transaction traitée par NotchPay (et inversement) —
        # sans ce garde-fou, une référence rejouée sur le mauvais endpoint webhook pourrait
        # créditer la mauvaise commande sans qu'aucun fonds supplémentaire ne soit payé.
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-cross-provider",
            provider="notchpay",  # traitée par NotchPay, pas KPay
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=self._body("ref-cross-provider"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_rejects_replayed_provider_reference(self, mock_verify):
        # Symétrique de PaymentSafetyTests.test_webhook_rejects_replayed_provider_reference :
        # un `paymentId` réel (donc `verify_payment` réussirait) mais appartenant à un AUTRE
        # paiement KPay ne doit jamais clôturer cette transaction — seul celui enregistré à la
        # création (`initiate_payment`) est accepté.
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-replay",
            provider="kpay",
            provider_reference="pay_legit",  # celle attribuée à CE paiement
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            # rejouée depuis un AUTRE paiement, réellement complété chez l'attaquant
            data=self._body(
                "ref-kpay-replay", payment_id="pay_from_another_real_payment"
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        mock_verify.assert_not_called()
        txn.refresh_from_db()
        self.assertEqual(txn.status, "pending")

    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_accepts_matching_provider_reference(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-match",
            provider="kpay",
            provider_reference="pay_match",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="pay_match",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=self._body("ref-kpay-match", payment_id="pay_match"),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        mock_verify.assert_called_once_with("pay_match")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")

    @override_settings(KPAY_WEBHOOK_SECRET="kpaysecret")
    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_rejects_bad_signature(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-badsig",
            provider="kpay",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=self._body("ref-kpay-badsig"),
            content_type="application/json",
            HTTP_X_KPAY_SIGNATURE="deadbeef",
        )

        self.assertEqual(response.status_code, 401)
        mock_verify.assert_not_called()

    @override_settings(KPAY_WEBHOOK_SECRET="kpaysecret")
    @patch("escrow.services.providers.kpay.KPayClient.verify_payment")
    def test_webhook_accepts_valid_signature(self, mock_verify):
        order = self._create_order()
        PaymentTransaction.objects.create(
            order=order,
            transaction_type="collect",
            external_ref="ref-kpay-goodsig",
            provider="kpay",
            amount=1030,
            channel="mtn",
            phone_number="+237611800000",
            status="pending",
        )
        mock_verify.return_value = ProviderResult(
            provider_reference="pay_goodsig",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )
        body = self._body("ref-kpay-goodsig")
        signature = hmac.new(b"kpaysecret", body.encode(), hashlib.sha256).hexdigest()

        client = APIClient()
        response = client.post(
            "/api/kpay-webhook/",
            data=body,
            content_type="application/json",
            HTTP_X_KPAY_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid_escrow")

    @patch("escrow.services.providers.kpay.KPayClient.initialize_payment")
    def test_initiate_payment_uses_active_provider(self, mock_init):
        # `initiate_payment` (OrderViewSet) doit utiliser le fournisseur ACTUELLEMENT actif
        # (PAYMENT_PROVIDER=kpay ici, via le décorateur de classe), pas NotchPay en dur.
        mock_init.return_value = ProviderResult(
            provider_reference="pay_new",
            status=STATUS_PENDING,
            raw={"id": "pay_new", "status": "PENDING"},
        )
        order = self._create_order()
        api_client = APIClient()
        api_client.force_authenticate(user=self.buyer)

        response = api_client.post(
            f"/api/orders/{order.id}/initiate_payment/",
            {"phone_number": "+237611800000", "channel": "mtn"},
        )

        self.assertEqual(response.status_code, 202)
        txn = PaymentTransaction.objects.get(order=order)
        self.assertEqual(txn.provider, "kpay")


class PaymentReconciliationMultiProviderTests(APITestCase):
    """La réconciliation (escrow/tasks.py) doit interroger le fournisseur qui a RÉELLEMENT
    traité chaque transaction (`PaymentTransaction.provider`), jamais un fournisseur unique
    codé en dur — sans quoi changer `PAYMENT_PROVIDER` casserait la réconciliation des
    reversements déjà en cours chez l'ancien fournisseur."""

    def setUp(self):
        self.buyer = User.objects.create_user(
            phone_number="+237611700000", first_name="Acheteur", last_name="Test"
        )
        self.seller = User.objects.create_user(
            phone_number="+237622700000", first_name="Vendeur", last_name="Test"
        )
        self.cat = Category.objects.create(name="Test", slug="test-reconcile")
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
            total_amount=Decimal("1030"),
            status="completed",
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def _age(self, txn, minutes):
        PaymentTransaction.objects.filter(pk=txn.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )

    @override_settings(PAYMENT_PROVIDER="kpay")
    @patch("escrow.services.providers.kpay.KPayClient.verify_transfer")
    @patch("escrow.services.providers.notchpay.NotchPayClient.verify_transfer")
    def test_reconciliation_queries_each_transaction_own_provider(
        self, mock_notchpay_verify, mock_kpay_verify
    ):
        notchpay_order = self._create_order()
        notchpay_txn = PaymentTransaction.objects.create(
            order=notchpay_order,
            transaction_type="withdraw",
            external_ref="notch-transfer-1",
            provider="notchpay",
            idempotency_key=f"release:{notchpay_order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622700000",
            status="pending",
        )
        self._age(notchpay_txn, minutes=60)

        kpay_order = self._create_order()
        kpay_txn = PaymentTransaction.objects.create(
            order=kpay_order,
            transaction_type="withdraw",
            external_ref="wdr_kpay_1",
            provider="kpay",
            idempotency_key=f"release:{kpay_order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622700000",
            status="pending",
        )
        self._age(kpay_txn, minutes=60)

        mock_notchpay_verify.return_value = ProviderResult(
            provider_reference="notch-transfer-1",
            status=STATUS_SUCCESSFUL,
            raw={"transfer": {"status": "complete"}},
        )
        mock_kpay_verify.return_value = ProviderResult(
            provider_reference="wdr_kpay_1",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )

        resolved, _ = reconcile_pending_payouts()

        self.assertEqual(resolved, 2)
        # Chaque mock n'a été appelé qu'avec SA propre référence — jamais avec celle de
        # l'autre fournisseur.
        mock_notchpay_verify.assert_called_once_with("notch-transfer-1")
        mock_kpay_verify.assert_called_once_with("wdr_kpay_1")
        notchpay_txn.refresh_from_db()
        kpay_txn.refresh_from_db()
        self.assertEqual(notchpay_txn.status, "successful")
        self.assertEqual(kpay_txn.status, "successful")

    @override_settings(PAYMENT_PROVIDER="kpay")
    @patch("escrow.services.providers.kpay.KPayClient.verify_transfer")
    def test_reconciliation_falls_back_to_active_provider_for_blank_legacy_rows(
        self, mock_kpay_verify
    ):
        # Ligne créée avant l'ajout du champ `provider` (blank=True, voir escrow/models.py) :
        # doit retomber sur le fournisseur actuellement actif plutôt que de planter.
        order = self._create_order()
        txn = PaymentTransaction.objects.create(
            order=order,
            transaction_type="withdraw",
            external_ref="legacy-ref",
            provider="",
            idempotency_key=f"release:{order.id}",
            amount=Decimal("970"),
            channel="mtn",
            phone_number="+237622700000",
            status="pending",
        )
        self._age(txn, minutes=60)
        mock_kpay_verify.return_value = ProviderResult(
            provider_reference="legacy-ref",
            status=STATUS_SUCCESSFUL,
            raw={"status": "COMPLETED"},
        )

        resolved, _ = reconcile_pending_payouts()

        self.assertEqual(resolved, 1)
        mock_kpay_verify.assert_called_once_with("legacy-ref")
